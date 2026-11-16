import argparse
import fcntl
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import requests
from confluent_kafka import Consumer, TopicPartition
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.schema import Schema
from pyiceberg.types import BooleanType, LongType, NestedField, StringType, TimestamptzType


TOPIC = 'bank.public.accounts'
BRONZE = 'demo.cdc_events'
SILVER = 'demo.accounts_history'
RAW_SCHEMA = Schema(
    NestedField(1, 'event_id', StringType(), required=False),
    NestedField(2, 'kafka_offset', LongType(), required=False),
    NestedField(3, 'payload', StringType(), required=False),
)
HISTORY_SCHEMA = Schema(
    NestedField(1, 'account_id', LongType(), required=False),
    NestedField(2, 'owner_name', StringType(), required=False),
    NestedField(3, 'status', StringType(), required=False),
    NestedField(4, 'valid_from', TimestamptzType(), required=False),
    NestedField(5, 'valid_to', TimestamptzType(), required=False),
    NestedField(6, 'is_current', BooleanType(), required=False),
    NestedField(7, 'version_offset', LongType(), required=False),
)
RAW_ARROW = pa.schema([
    ('event_id', pa.string()), ('kafka_offset', pa.int64()), ('payload', pa.string()),
])
HISTORY_ARROW = pa.schema([
    ('account_id', pa.int64()), ('owner_name', pa.string()), ('status', pa.string()),
    ('valid_from', pa.timestamp('us', tz='UTC')),
    ('valid_to', pa.timestamp('us', tz='UTC')),
    ('is_current', pa.bool_()), ('version_offset', pa.int64()),
])


def catalog():
    for attempt in range(30):
        try:
            return load_catalog('local', **{
                'type': 'rest', 'uri': 'http://rest:8181', 'warehouse': 's3://warehouse/',
                's3.endpoint': 'http://minio:9000', 's3.access-key-id': 'demo_admin',
                's3.secret-access-key': 'demo_password', 's3.region': 'us-east-1',
                's3.force-virtual-addressing': 'false',
            })
        except requests.exceptions.ConnectionError:
            if attempt == 29:
                raise
            time.sleep(2)


def table(client, name, schema):
    try:
        return client.create_table(name, schema=schema)
    except TableAlreadyExistsError:
        return client.load_table(name)


def event_time(payload):
    source = payload['source']
    if source.get('ts_us') is not None:
        return datetime.fromtimestamp(source['ts_us'] / 1_000_000, timezone.utc)
    return datetime.fromtimestamp(source['ts_ms'] / 1000, timezone.utc)


def history(events):
    versions = []
    active = {}
    fingerprints = set()
    last_time = {}
    for raw in sorted(events, key=lambda event: event['kafka_offset']):
        payload = json.loads(raw['payload'])
        operation = payload['op']
        if operation not in ('r', 'c', 'u', 'd'):
            raise ValueError(f'Unsupported CDC operation {operation}')
        record = payload['after'] if operation != 'd' else payload['before']
        if record is None:
            raise ValueError('Missing before/after in CDC event')
        key = record['account_id']
        identity = {
            'lsn': payload['source'].get('lsn'),
            'txId': payload['source'].get('txId'),
            'op': operation, 'before': payload.get('before'), 'after': payload.get('after'),
        }
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        moment = event_time(payload)
        if key in last_time and moment < last_time[key]:
            raise ValueError(f'Source clock moved backwards for account {key}')
        last_time[key] = moment
        previous = active.get(key)
        if operation != 'd' and previous and all(
            previous[field] == record[field] for field in ('owner_name', 'status')
        ):
            continue
        if previous:
            previous['valid_to'] = moment
            previous['is_current'] = False
            del active[key]
        if operation != 'd':
            version = dict(
                account_id=key, owner_name=record['owner_name'], status=record['status'],
                valid_from=moment, valid_to=None, is_current=True,
                version_offset=raw['kafka_offset'],
            )
            versions.append(version)
            active[key] = version
    return versions


def run(replay=False):
    client = catalog()
    try:
        client.create_namespace('demo')
    except NamespaceAlreadyExistsError:
        pass
    bronze = table(client, BRONZE, RAW_SCHEMA)
    silver = table(client, SILVER, HISTORY_SCHEMA)
    stored = bronze.scan().to_arrow().to_pylist()
    known = {event['event_id'] for event in stored}
    consumer = Consumer({
        'bootstrap.servers': 'kafka:9092', 'group.id': 'accounts-scd2',
        'enable.auto.commit': False, 'enable.auto.offset.store': False,
        'auto.offset.reset': 'earliest',
    })
    try:
        metadata = consumer.list_topics(TOPIC, timeout=30)
        topic = metadata.topics.get(TOPIC)
        if topic is None or topic.error:
            raise RuntimeError(f'CDC topic is unavailable: {topic}')
        if set(topic.partitions) != {0}:
            raise RuntimeError('This demonstration requires exactly one Kafka partition')
        # Bronze is the durable checkpoint. Replaying it also repairs a failed Silver commit.
        start = 0 if replay else max((row['kafka_offset'] for row in stored), default=-1) + 1
        consumer.assign([TopicPartition(TOPIC, 0, start)])
        _, end = consumer.get_watermark_offsets(TopicPartition(TOPIC, 0), timeout=30, cached=False)
        new = []
        next_offset = start
        deadline = time.monotonic() + 90
        while next_offset < end:
            if time.monotonic() > deadline:
                raise TimeoutError(f'Kafka read timeout at {next_offset}, expected {end}')
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                raise RuntimeError(message.error())
            next_offset = message.offset() + 1
            if message.value() is None:
                continue
            identifier = f'{message.topic()}/{message.partition()}/{message.offset()}'
            if identifier not in known:
                payload = json.loads(message.value())
                if 'payload' in payload and 'op' not in payload:
                    payload = payload['payload']
                new.append(dict(event_id=identifier, kafka_offset=message.offset(),
                                payload=json.dumps(payload, ensure_ascii=False)))
                known.add(identifier)
        if new:
            bronze.append(pa.Table.from_pylist(new, schema=RAW_ARROW))
            stored.extend(new)
        # Always rebuild from durable Bronze, even when no new messages were found.
        versions = history(stored)
        silver.overwrite(pa.Table.from_pylist(versions, schema=HISTORY_ARROW))
        consumer.commit(offsets=[TopicPartition(TOPIC, 0, next_offset)], asynchronous=False)
        result = dict(new_events=len(new), bronze_rows=len(stored), silver_rows=len(versions),
                      next_offset=next_offset, replay=replay)
        print(json.dumps(result), flush=True)
        Path('/evidence').mkdir(exist_ok=True)
        output = 'replay.json' if replay else 'last_batch.json'
        Path('/evidence', output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        consumer.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay', action='store_true')
    arguments = parser.parse_args()
    with open('/lock/worker.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(arguments.replay)
