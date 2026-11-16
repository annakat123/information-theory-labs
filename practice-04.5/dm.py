"""DM: витрина-агрегат по месяцам (модель AGGREGATE KEY — пред-агрегация).

Строит dw.dm_agg_<область>: сумма value по (id, месяц). Строки с одинаковым ключом
при загрузке СУММИРУЮТСЯ (SUM) — это и есть смысл слоя DM.

Запуск:
  docker exec sr-py python /app/dm.py --domain oil-gas
"""
import argparse

import pymysql

HOST, PORT, USER, PASSWORD = "host.docker.internal", 9030, "root", ""
DB = "dw"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="oil-gas | metallurgy | chemical | cs")
    args = ap.parse_args()
    d = args.domain.replace("-", "_")

    c = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                        database=DB, autocommit=True)
    cur = c.cursor()

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS dw.dm_agg_{d} (
            id VARCHAR(20),
            ym VARCHAR(7),
            total_value DOUBLE SUM
        ) AGGREGATE KEY(id, ym) DISTRIBUTED BY HASH(id) BUCKETS 1""")
    cur.execute(f"TRUNCATE TABLE dw.dm_agg_{d}")

    cur.execute(f"""
        INSERT INTO dw.dm_agg_{d}
        SELECT id, date_format(date, '%Y-%m') AS ym, value
        FROM dw.ods_fact_{d}""")

    cur.execute(f"SELECT count(*) FROM dw.dm_agg_{d}")
    n = cur.fetchone()[0]
    c.close()
    print(f"[dm] витрина dw.dm_agg_{d} готова: {n} строк")


if __name__ == "__main__":
    main()
