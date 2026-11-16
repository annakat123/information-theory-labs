"""MCP-сервер: отдаёт агенту инструмент run_sql(sql) -> StarRocks (read-only).

Это «мост» между LLM-агентом и StarRocks по протоколу MCP (Model Context Protocol).
Разрешаем только чтение: SELECT / SHOW / DESCRIBE / EXPLAIN. Остальное отклоняем.

Запуск сервера производит сам MCP-клиент (agent.py), поэтому руками его запускать
не нужно. Проверить инструмент можно так (см. гайд §8).
"""
import logging

import pymysql
from mcp.server.fastmcp import FastMCP

# Глушим INFO-логи MCP-протокола (ListToolsRequest / CallToolRequest / ...),
# чтобы вывод у студента был чистым.
logging.getLogger("mcp").setLevel(logging.WARNING)

HOST, PORT, USER, PASSWORD = "host.docker.internal", 9030, "root", ""
DB = "dw"

mcp = FastMCP("starrocks")


def _query(sql):
    c = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                        database=DB, autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        c.close()
    return rows


@mcp.tool()
def run_sql(sql: str) -> str:
    """Выполнить read-only запрос в StarRocks (база dw) и вернуть строки текстом.

    Таблицы: stg_* / ods_* / dm_* (смотри SHOW TABLES FROM dw, DESCRIBE dw.<таблица>).
    """
    head = sql.strip().lstrip("(").strip().lower()
    if not head.startswith(("select", "show", "describe", "explain")):
        return "ERROR: разрешён только read-only (SELECT / SHOW / DESCRIBE / EXPLAIN)"
    try:
        rows = _query(sql)
    except Exception as e:  # noqa: BLE001 — возвращаем ошибку агенту, а не роняем сервер
        return f"ERROR: {e}"
    if not rows:
        return "(пусто)"
    return "\n".join("\t".join(str(x) for x in row) for row in rows)


if __name__ == "__main__":
    mcp.run()
