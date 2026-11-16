"""Run the local CDC demonstration and save the actual verification results."""
import csv
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / 'evidence'
EVIDENCE.mkdir(exist_ok=True)


def command(arguments, filename=None):
    result = subprocess.run(arguments, cwd=ROOT, encoding='utf-8', errors='replace',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if filename:
        content = result.stdout + result.stderr if filename.endswith('.log') else result.stdout
        (EVIDENCE / filename).write_text(content, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def compose(*arguments, filename=None):
    return command(['docker', 'compose', *arguments], filename)


def sql(query, filename=None):
    output = compose('exec', '-T', 'trino', 'trino', '--execute', query,
                     '--output-format', 'JSON', filename=filename)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def batch(replay=False):
    args = ['run', '--rm', 'worker']
    if replay:
        args.append('--replay')
    output = compose(*args)
    with (EVIDENCE / 'worker.log').open('a', encoding='utf-8') as log:
        log.write(output)
    return json.loads(output.strip().splitlines()[-1])


def connector():
    config = json.loads((ROOT / 'config/accounts.json').read_text(encoding='utf-8'))
    url = 'http://127.0.0.1:18083/connectors/accounts-cdc/config'
    request = urllib.request.Request(url, data=json.dumps(config['config']).encode(),
                                     headers={'Content-Type': 'application/json'}, method='PUT')
    for attempt in range(60):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
            break
        except (urllib.error.URLError, TimeoutError):
            if attempt == 59:
                raise
            time.sleep(1)
    status_url = 'http://127.0.0.1:18083/connectors/accounts-cdc/status'
    for attempt in range(60):
        with urllib.request.urlopen(status_url, timeout=5) as response:
            status = json.load(response)
        tasks = status.get('tasks', [])
        if tasks and all(task['state'] == 'RUNNING' for task in tasks):
            (EVIDENCE / 'connector_status.json').write_text(
                json.dumps(status, indent=2), encoding='utf-8')
            return
        if any(task['state'] == 'FAILED' for task in tasks):
            raise RuntimeError(json.dumps(status))
        time.sleep(1)
    raise TimeoutError('Debezium did not start')


def main():
    compose('up', '-d')
    compose('build', 'worker', filename='build.log')
    connector()
    initial = batch()
    for _ in range(10):
        if initial['bronze_rows'] >= 3:
            break
        time.sleep(1)
        initial = batch()
    if initial['bronze_rows'] < 3:
        raise RuntimeError('Initial snapshot is incomplete')
    sql('SELECT * FROM iceberg.demo.accounts_history ORDER BY account_id, version_offset',
        'before.jsonl')
    compose('exec', '-T', 'postgres', 'psql', '-U', 'bank', '-d', 'bank', '-v', 'ON_ERROR_STOP=1',
            '-c', "UPDATE accounts SET owner_name='Соколов Максим' "
                  "WHERE account_id=1001 AND owner_name <> 'Соколов Максим'", filename='change_owner.txt')
    compose('exec', '-T', 'postgres', 'psql', '-U', 'bank', '-d', 'bank', '-v', 'ON_ERROR_STOP=1',
            '-c', "UPDATE accounts SET status='blocked' WHERE account_id=1002 AND status <> 'blocked'",
            filename='change_status.txt')
    compose('exec', '-T', 'postgres', 'psql', '-U', 'bank', '-d', 'bank', '-v', 'ON_ERROR_STOP=1',
            '-c', "INSERT INTO accounts VALUES (1004, 'Орлова Мария', 'active') ON CONFLICT DO NOTHING",
            filename='insert_account.txt')
    compose('exec', '-T', 'postgres', 'psql', '-U', 'bank', '-d', 'bank', '-v', 'ON_ERROR_STOP=1',
            '-c', 'DELETE FROM accounts WHERE account_id=1003', filename='delete_account.txt')
    for attempt in range(10):
        current = batch()
        if current['bronze_rows'] >= 7:
            break
        time.sleep(1)
    if current['bronze_rows'] != 7 or current['silver_rows'] != 6:
        raise AssertionError(f'Unexpected number of rows: {current}')
    order = 'SELECT * FROM iceberg.demo.accounts_history ORDER BY account_id, version_offset'
    before_replay = sql(order, 'history.jsonl')
    repeated = batch(replay=True)
    after_replay = sql(order, 'history_after_replay.jsonl')
    assert before_replay == after_replay, 'Replay changed SCD history'
    assert repeated['new_events'] == 0, 'Replay duplicated Bronze events'
    active = sql('SELECT account_id, owner_name, status FROM iceberg.demo.accounts_history '
                 'WHERE is_current ORDER BY account_id', 'current.jsonl')
    assert [row['account_id'] for row in active] == [1001, 1002, 1004]
    assert active[0]['owner_name'] == 'Соколов Максим'
    assert active[1]['status'] == 'blocked'
    history_1001 = [row for row in before_replay if row['account_id'] == 1001]
    assert len(history_1001) == 2
    old_moment = history_1001[0]['valid_from']
    point_query = ("SELECT account_id, owner_name, status FROM iceberg.demo.accounts_history "
                   f"WHERE account_id=1001 AND valid_from <= TIMESTAMP '{old_moment}' "
                   f"AND (valid_to IS NULL OR TIMESTAMP '{old_moment}' < valid_to)")
    point_result = sql(point_query, 'point_in_time.jsonl')
    assert len(point_result) == 1 and point_result[0]['owner_name'] == 'Иванов Илья'
    (EVIDENCE / 'point_in_time.sql').write_text(point_query + ';\n', encoding='utf-8')
    invalid = sql('SELECT count(*) AS invalid FROM iceberg.demo.accounts_history '
                  'WHERE valid_to < valid_from OR (is_current AND valid_to IS NOT NULL) '
                  'OR (NOT is_current AND valid_to IS NULL)')
    assert invalid[0]['invalid'] == 0
    with (EVIDENCE / 'history.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=before_replay[0].keys())
        writer.writeheader()
        writer.writerows(before_replay)
    compose('ps', '-a', filename='docker_compose_ps.txt')
    command(['docker', 'ps', '--filter', 'label=com.docker.compose.project=vk-practice2-but'],
            filename='docker_ps.txt')
    summary = dict(status='PASS', bronze_events=7, history_versions=6,
                   current_accounts=3, replay_unchanged=True, point_in_time_passed=True,
                   delete_closed_history=True, invalid_intervals=0)
    (EVIDENCE / 'verification.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
