"""Проверка MCP-инструмента run_sql (и read-only-защиты) без inline-скриптов.

Запуск (внутри python-контейнера):
  docker exec sr-py python /app/mcp_check.py --domain oil-gas

Делает: list_tools -> SELECT count(*) из stg_fact_<область> -> DROP (должен дать ERROR).
"""
import argparse
import asyncio
import logging

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logging.getLogger("mcp").setLevel(logging.WARNING)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="oil-gas",
                    help="oil-gas | metallurgy | chemical | cs")
    args = ap.parse_args()
    fact = "stg_fact_" + args.domain.replace("-", "_")

    async with stdio_client(StdioServerParameters(command="python",
                                                  args=["/app/mcp_server.py"])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = await s.list_tools()
            print("инструменты:", [t.name for t in tools.tools])

            ok = await s.call_tool("run_sql",
                                   arguments={"sql": f"SELECT count(*) FROM dw.{fact}"})
            print("SELECT count(*):", ok.content[0].text)

            bad = await s.call_tool("run_sql",
                                    arguments={"sql": f"DROP TABLE dw.{fact}"})
            print("DROP (должен быть ERROR):", bad.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
