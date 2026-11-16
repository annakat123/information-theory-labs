"""Агент данных: LLM (Ollama) + MCP run_sql (StarRocks) + RAG (Qdrant).

Сам решает, куда идти за ответом. Четыре режима:
  none  — без инструментов (чистая LLM, галлюцинирует)
  sql   — только SQL в StarRocks (через MCP run_sql)
  docs  — только поиск по документам (Qdrant)
  both  — симбиоз: документ даёт методику, SQL — цифры

Перед написанием SQL агент узнаёт схему БД, вызывая через MCP
SHOW TABLES + DESCRIBE (это и есть «агент читает БД через протокол»).

Запуск (внутри python-контейнера):
  docker exec sr-py python /app/agent.py --domain oil-gas --mode both "Сколько добыла скважина W-02 в январе 2026?"
"""
import argparse
import asyncio
import logging
import re

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

# Глушим INFO-логи MCP (ListTools/CallTool/Processing request), чтобы не мешали ответу.
logging.getLogger("mcp").setLevel(logging.WARNING)

OLLAMA_URL = "http://host.docker.internal:11434"
QDRANT_HOST, QDRANT_PORT = "host.docker.internal", 6334
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LLM = "qwen2.5:1.5b"
COLLECTION = "chunks_500"          # коллекция, которую строит embed.py

_emb = None


def emb():
    global _emb
    if _emb is None:
        _emb = SentenceTransformer(MODEL_NAME)
    return _emb


def chat(prompt):
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": LLM, "stream": False, "options": {"temperature": 0, "seed": 42, "num_predict": 512},
              "messages": [{"role": "user", "content": prompt}]},
        timeout=600,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def search_docs(question, k=3):
    qd = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    qv = emb().encode(question).tolist()
    hits = qd.query_points(collection_name=COLLECTION, query=qv, limit=k).points
    return "\n\n".join(h.payload["chunk_text"] for h in hits)


async def run_sql_via_mcp(sql):
    async with stdio_client(StdioServerParameters(command="python",
                                                  args=["/app/mcp_server.py"])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("run_sql", arguments={"sql": sql})
            return res.content[0].text


async def discover_schema(domain):
    """Схема цепочки DWH своей области через MCP: SHOW TABLES + DESCRIBE.

    Отдаём модели только таблицы своей области (суффикс `_<domain>`) в компактном
    виде `таблица(колонка, ...)`, а не весь каталог dw: research.py кладёт туда
    служебные r_dup/r_pk/r_agg (без суффикса), на которых мелкая LLM строит ломаные
    JOIN. Компактный вид проще для модели, чем сырой DESCRIBE (Field/Type/Null/...).
    """
    suffix = "_" + domain.replace("-", "_")
    tables = (await run_sql_via_mcp("SHOW TABLES FROM dw")).splitlines()
    parts = []
    for t in tables:
        t = t.strip()
        if not t or not t.endswith(suffix):
            continue
        desc = await run_sql_via_mcp(f"DESCRIBE dw.{t}")
        cols = [ln.split("\t")[0].strip() for ln in desc.splitlines() if ln.strip()]
        parts.append(f"{t}({', '.join(cols)})")
    return "\n".join(parts)


def _clean_sql(s):
    """Приводим ответ модели к голому SQL-запросу.

    Маленькие модели (qwen2.5) оборачивают запрос в ```sql ... ``` и ставят ';'
    в конце, даже если их попросили не делать этого. Без очистки read-only-защита
    в MCP отклонила бы корректный SELECT. Снимаем ограждение и хвостовые символы,
    а если перед запросом осталась «проза» — берём с первого ключевого слова.
    """
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)   # открывающий ```sql
    s = re.sub(r"\s*```$", "", s)            # закрывающий ```
    s = s.strip().rstrip(";").strip()
    m = re.search(r"(?i)\b(select|show|describe|explain)\b", s)
    if m and m.start() > 0:
        s = s[m.start():]
    return s


def sql_from_llm(question, schema):
    prompt = (
        "Ты пишешь SQL для StarRocks (база dw). Верни ТОЛЬКО один запрос SELECT, "
        "без пояснений и без точки с запятой.\n"
        "Обычно вопрос решается по одной таблице. JOIN используй только если он "
        "действительно нужен.\n"
        "Колонки: id — код объекта; date — дата; value — числовое значение "
        "(добыча/выплавка/выпуск/обработка); name — название; status — статус; "
        "owner — ответственный.\n"
        f"Схема (таблица: колонки):\n{schema}\n\n"
        f"Вопрос: {question}"
    )
    return _clean_sql(chat(prompt))


async def answer(question, mode, domain):
    if mode == "none":
        return chat(f"Ответь коротко, без внешних данных.\nВопрос: {question}")

    if mode == "docs":
        ctx = search_docs(question)
        return chat("Отвечай ТОЛЬКО по контексту. Если ответа нет — скажи, что не знаешь.\n\n"
                    f"Контекст:\n{ctx}\n\nВопрос: {question}")

    if mode == "sql":
        schema = await discover_schema(domain)
        sql = sql_from_llm(question, schema)
        rows = await run_sql_via_mcp(sql)
        return chat(f"Отвечай по результату SQL.\nSQL: {sql}\nРезультат:\n{rows}\n\nВопрос: {question}")

    if mode == "both":
        ctx = search_docs(question)
        schema = await discover_schema(domain)
        sql = sql_from_llm(question, schema)
        rows = await run_sql_via_mcp(sql)
        return chat("Отвечай по контексту (методика из документа) и по результату SQL (цифры).\n"
                    f"Контекст (документ):\n{ctx}\n\nSQL: {sql}\nРезультат:\n{rows}\n\nВопрос: {question}")

    raise ValueError(f"неизвестный режим: {mode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True,
                    help="oil-gas | metallurgy | chemical | cs")
    ap.add_argument("--mode", required=True, choices=["none", "sql", "docs", "both"])
    ap.add_argument("question")
    args = ap.parse_args()

    print(f"=== режим: {args.mode} ({args.domain}) ===\n")
    print(asyncio.run(answer(args.question, args.mode, args.domain)))


if __name__ == "__main__":
    main()
