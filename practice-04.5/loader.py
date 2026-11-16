"""STG: загрузка CSV своей области в StarRocks (модель DUPLICATE KEY).

Создаёт три таблицы dw.stg_<область> и грузит в них CSV через pymysql.

Запуск (внутри python-контейнера):
  docker exec sr-py python /app/loader.py --domain oil-gas
"""
import argparse
import csv
import os

import pymysql

HOST, PORT, USER, PASSWORD = "host.docker.internal", 9030, "root", ""
DB = "dw"

DIM_COLS = ["id", "name", "status", "owner"]
CHG_COLS = ["id", "status", "owner"]
FACT_COLS = ["id", "date", "value"]


def conn():
    return pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                           database=DB, autocommit=True)


def create_tables(cur, d):
    """Три STG-таблицы с моделью DUPLICATE KEY (храним всё как есть)."""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS dw.stg_dim_{d} (
            id VARCHAR(20), name VARCHAR(60), status VARCHAR(30), owner VARCHAR(60)
        ) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1""")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS dw.stg_changes_{d} (
            id VARCHAR(20), status VARCHAR(30), owner VARCHAR(60)
        ) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1""")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS dw.stg_fact_{d} (
            id VARCHAR(20), date DATE, value DOUBLE
        ) DUPLICATE KEY(id, date) DISTRIBUTED BY HASH(id) BUCKETS 1""")


def load_csv(cur, table, path, cols):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ph = ",".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})"
    cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    print(f"[load] {path} -> {table}: {len(rows)} строк")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="oil-gas | metallurgy | chemical | cs")
    args = ap.parse_args()

    d = args.domain.replace("-", "_")          # имя папки oil-gas -> суффикс таблиц oil_gas
    base = f"/app/data/{args.domain}"

    c = conn()
    try:
        cur = c.cursor()
        create_tables(cur, d)
        cur.execute(f"TRUNCATE TABLE dw.stg_dim_{d}")
        cur.execute(f"TRUNCATE TABLE dw.stg_changes_{d}")
        cur.execute(f"TRUNCATE TABLE dw.stg_fact_{d}")
        load_csv(cur, f"dw.stg_dim_{d}", f"{base}/dim.csv", DIM_COLS)
        load_csv(cur, f"dw.stg_changes_{d}", f"{base}/changes.csv", CHG_COLS)
        load_csv(cur, f"dw.stg_fact_{d}", f"{base}/fact.csv", FACT_COLS)
    finally:
        c.close()
    print("[load] готово")


if __name__ == "__main__":
    main()
