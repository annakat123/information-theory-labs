"""Исследование: модель таблицы StarRocks (Duplicate / Aggregate / Primary Key).

Один и тот же набор фактов грузим в ТРИ модели и меряем:
  * строки — count(*): сколько физических строк хранит модель (это и есть «объём»)
  * время  — запрос «сумма value по каждому id» (мс)

Чтобы разница во времени была видна на глаз, данные «размножаем» повтором (--repeat).
По умолчанию факт повторяется 5000 раз (~десятки тысяч строк) — этого хватает, чтобы
DUPLICATE (хранит всё) заметно проиграл AGGREGATE (пред-агрегат) и PRIMARY KEY (дедуп).

Запуск:
  docker exec sr-py python /app/research.py --domain oil-gas
  docker exec sr-py python /app/research.py --domain oil-gas --repeat 20000

Вывод — таблица «модель → строки / время», которую заполняешь в §7 гайда.
"""
import argparse
import csv
import time

import pymysql

HOST, PORT, USER, PASSWORD = "host.docker.internal", 9030, "root", ""
DB = "dw"


def conn():
    return pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                           database=DB, autocommit=True)


def insert_batched(cur, sql, rows, batch=5000):
    for i in range(0, len(rows), batch):
        cur.executemany(sql, rows[i:i + batch])


def time_query(cur, sql, n=3):
    """Минимальное время из n прогонов (устойчивее к шуму)."""
    best = float("inf")
    for _ in range(n):
        t0 = time.perf_counter()
        cur.execute(sql)
        cur.fetchall()
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="oil-gas | metallurgy | chemical | cs")
    ap.add_argument("--repeat", type=int, default=5000,
                    help="во сколько раз размножить факты (увеличь, если разрыв не виден)")
    args = ap.parse_args()

    with open(f"/app/data/{args.domain}/fact.csv", newline="", encoding="utf-8") as f:
        facts = [(r["id"], r["date"], float(r["value"])) for r in csv.DictReader(f)]

    c = conn()
    cur = c.cursor()

    # три модели для одних и тех же строк
    cur.execute("CREATE TABLE IF NOT EXISTS dw.r_dup "
                "(id VARCHAR(20), date DATE, value DOUBLE) "
                "DUPLICATE KEY(id, date) DISTRIBUTED BY HASH(id) BUCKETS 1")
    cur.execute("CREATE TABLE IF NOT EXISTS dw.r_pk "
                "(id VARCHAR(20), date DATE, value DOUBLE) "
                "PRIMARY KEY(id, date) DISTRIBUTED BY HASH(id) BUCKETS 1")
    cur.execute("CREATE TABLE IF NOT EXISTS dw.r_agg "
                "(id VARCHAR(20), value DOUBLE SUM) "
                "AGGREGATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1")
    for t in ("r_dup", "r_pk", "r_agg"):
        cur.execute(f"TRUNCATE TABLE dw.{t}")

    dup_rows = facts * args.repeat                                  # (id, date, value)
    agg_rows = [(i, v) for (i, _d, v) in facts] * args.repeat       # (id, value) — SUM по id

    insert_batched(cur, "INSERT INTO dw.r_dup (id, date, value) VALUES (%s, %s, %s)", dup_rows)
    insert_batched(cur, "INSERT INTO dw.r_pk  (id, date, value) VALUES (%s, %s, %s)", dup_rows)
    insert_batched(cur, "INSERT INTO dw.r_agg (id, value) VALUES (%s, %s)", agg_rows)

    print(f"[research] загружено фактов: {len(facts)} x {args.repeat} = {len(dup_rows)}\n")

    # запрос, который у каждой модели «свой»: у dup/pk это GROUP BY на лету,
    # у agg — уже готовый пред-агрегат (GROUP BY не нужен)
    q = {
        "r_dup": "SELECT id, SUM(value) AS s FROM dw.r_dup GROUP BY id",
        "r_pk":  "SELECT id, SUM(value) AS s FROM dw.r_pk GROUP BY id",
        "r_agg": "SELECT id, value AS s FROM dw.r_agg",
    }
    print("| модель | строк | время запроса (мс) |")
    print("|---|---|---|")
    for t, sql in q.items():
        cur.execute(f"SELECT count(*) FROM dw.{t}")
        rows = cur.fetchone()[0]
        ms = time_query(cur, sql) * 1000
        print(f"| {t} ({ 'Duplicate' if t=='r_dup' else 'Aggregate' if t=='r_agg' else 'Primary' }) "
              f"| {rows} | {ms:.1f} |")

    c.close()
    print("\n[research] вывод: Duplicate хранит всё (много строк, детально), "
          "Primary дедуплицирует по ключу и умеет upsert (поэтому dim-SCD2 — на нём), "
          "Aggregate пред-агрегирует (мало строк и быстрее агрегаты, но теряет детализацию).")


if __name__ == "__main__":
    main()
