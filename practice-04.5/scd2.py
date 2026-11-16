"""ODS: из STG строим измерение со SCD2 (Primary Key по sk) + факт.

- ods_dim_<область> : PRIMARY KEY(sk) -> история (SCD2), valid_from/valid_to/is_current
- ods_fact_<область>: DUPLICATE KEY (события как есть)

Применяет изменения из stg_changes_<область>: закрыть старую версию (UPDATE) +
вставить новую (INSERT). Проверка истории «как было на дату X» — в гайде (§6).

Запуск:
  docker exec sr-py python /app/scd2.py --domain oil-gas
"""
import argparse

import pymysql

HOST, PORT, USER, PASSWORD = "host.docker.internal", 9030, "root", ""
DB = "dw"

SNAPSHOT_DATE = "2026-01-01"   # дата начального снапшота
CHANGE_DATE = "2026-03-01"     # дата, когда применяем изменения


def conn():
    return pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                           database=DB, autocommit=True)


def build(d):
    c = conn()
    cur = c.cursor()

    # --- таблицы ODS ---
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS dw.ods_dim_{d} (
            sk BIGINT, id VARCHAR(20), name VARCHAR(60), status VARCHAR(30), owner VARCHAR(60),
            valid_from DATE, valid_to DATE, is_current BOOLEAN
        ) PRIMARY KEY(sk) DISTRIBUTED BY HASH(sk) BUCKETS 1""")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS dw.ods_fact_{d} (
            id VARCHAR(20), date DATE, value DOUBLE
        ) DUPLICATE KEY(id, date) DISTRIBUTED BY HASH(id) BUCKETS 1""")

    cur.execute(f"TRUNCATE TABLE dw.ods_dim_{d}")
    cur.execute(f"TRUNCATE TABLE dw.ods_fact_{d}")

    # --- начальный снапшот ---
    # суррогатный ключ sk = порядковый номер строки, все версии is_current = TRUE
    cur.execute(f"""
        INSERT INTO dw.ods_dim_{d} (sk, id, name, status, owner, valid_from, valid_to, is_current)
        SELECT row_number() OVER (ORDER BY id) AS sk, id, name, status, owner,
               DATE '{SNAPSHOT_DATE}', NULL, TRUE
        FROM dw.stg_dim_{d}""")
    # факт
    cur.execute(f"INSERT INTO dw.ods_fact_{d} SELECT id, date, value FROM dw.stg_fact_{d}")

    # --- применяем изменения (SCD2: закрыть старую + вставить новую) ---
    cur.execute(f"SELECT id, status, owner FROM dw.stg_changes_{d} ORDER BY id")
    changes = cur.fetchall()

    applied = 0
    for (oid, status, owner) in changes:
        cur.execute(f"SELECT sk, status, owner FROM dw.ods_dim_{d} WHERE id=%s AND is_current=TRUE", (oid,))
        row = cur.fetchone()
        if row is None:
            continue
        sk, old_status, old_owner = row
        if (old_status, old_owner) == (status, owner):
            continue  # атрибуты не изменились — новая версия не нужна

        cur.execute(f"SELECT COALESCE(MAX(sk), 0) + 1 FROM dw.ods_dim_{d}")
        new_sk = cur.fetchone()[0]
        cur.execute(f"SELECT name FROM dw.ods_dim_{d} WHERE sk=%s", (sk,))
        name = cur.fetchone()[0]

        # 1) закрыть текущую версию
        cur.execute(f"UPDATE dw.ods_dim_{d} SET valid_to=DATE '{CHANGE_DATE}', is_current=FALSE WHERE sk=%s", (sk,))
        # 2) вставить новую версию (новый sk)
        cur.execute(
            f"INSERT INTO dw.ods_dim_{d} (sk, id, name, status, owner, valid_from, valid_to, is_current) "
            f"VALUES (%s, %s, %s, %s, %s, DATE '{CHANGE_DATE}', NULL, TRUE)",
            (new_sk, oid, name, status, owner))
        applied += 1

    cur.execute(f"SELECT count(*) FROM dw.ods_dim_{d} WHERE is_current=TRUE")
    current = cur.fetchone()[0]
    c.close()

    print(f"[scd2] изменений применено: {applied}; текущих версий (is_current=TRUE): {current}")
    print("[scd2] история: посмотри запрос «как было на дату X» в гайде §6")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="oil-gas | metallurgy | chemical | cs")
    args = ap.parse_args()
    build(args.domain.replace("-", "_"))


if __name__ == "__main__":
    main()
