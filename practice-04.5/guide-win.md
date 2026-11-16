# Лаба 4.5 — гайд для Windows (PowerShell)

То же самое, что [`guide.md`](guide.md), но команды под **PowerShell** (Windows). Объяснения
«почему» — в `guide.md`; здесь — готовые команды и только то, что на Windows отличается.

> **Своя область** (подставляй вместо `oil-gas`; в именах таблиц дефис → `_`):
> 1 = Нефтегаз → `oil-gas`; 2 = Металлургия → `metallurgy`; 3 = Химтех → `chemical`; 4 = ИВТ → `cs`.

## Чем Windows отличается от macOS/Linux

| Момент | macOS/Linux | Windows (PowerShell) |
|---|---|---|
| Терминал | bash/zsh | **PowerShell** (пуск → «PowerShell») |
| Текущая папка в `docker -v` | `"$(pwd):/app"` | `"${PWD}:/app"` |
| Проверка порта | `lsof -iTCP:…` | `netstat -ano | findstr :9030` |
| HTTP-запрос | `curl -s …` | `Invoke-RestMethod …` (или `curl.exe -s …`) |
| Удалить папку | `rm -rf …` | `Remove-Item -Recurse -Force …` |

Всё остальное (`docker run`, `docker exec … python/mysql`, `ollama pull`) — **одинаково**,
потому что команды выполняются внутри Linux-контейнеров. Поэтому многострочных `python -c`
в гайде нет — проверку MCP вынесли в файл `mcp_check.py`.

> Если у тебя **Git Bash** (часть Git for Windows) — можно пользоваться `guide.md` как есть,
> заменив только `$(pwd)` на `$(pwd)` (в Git Bash это работает) и `lsof` пропустив.

## 0. Перед стартом

```powershell
# Docker Desktop запущен. Проверь, что порты свободны (пусто = ок):
netstat -ano | findstr ":9030 :6333 :11434"
```

## 1. StarRocks

```powershell
docker run -d --name starrocks -p 9030:9030 -p 8030:8030 -p 8040:8040 starrocks/allin1-ubuntu:3.3.11
```

Подожди 30–60 с и создай базу `dw`:

```powershell
docker exec starrocks mysql -P 9030 -h 127.0.0.1 -u root -e "CREATE DATABASE IF NOT EXISTS dw; SHOW DATABASES;"
```

## 2. python-контейнер + зависимости

```powershell
docker run -d --name sr-py --add-host host.docker.internal:host-gateway -v "${PWD}:/app" -v hf-cache:/root/.cache/huggingface python:3.12-slim sleep infinity
```

> Обрати внимание: `${PWD}` (не `$(pwd)`). Если используешь **cmd**, пиши `%cd%`.

```powershell
docker exec sr-py sh -c "pip install -q torch --index-url https://download.pytorch.org/whl/cpu"
docker exec sr-py sh -c "pip install -q -r /app/requirements.txt"
docker exec sr-py python -c "import pymysql, qdrant_client, sentence_transformers, mcp, requests; print('ok')"
```

Последняя строка должна напечатать `ok`.

## 3. Qdrant + Ollama + модель

```powershell
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant:latest
docker run -d --name ollama -p 11434:11434 -v ollama-models:/root/.ollama ollama/ollama:latest
docker exec ollama ollama pull qwen2.5:1.5b
```

> Модель ~1 ГБ — команда выше заблокирует терминал на несколько минут. Можно запустить её
> заранее в отдельном окне PowerShell, пока делаешь шаги 1–2 и 4–6.

Проверка:

```powershell
docker exec ollama ollama list
Invoke-RestMethod http://localhost:6333/collections
```

## 4. STG — сырые данные

```powershell
docker exec sr-py python /app/loader.py --domain oil-gas
docker exec starrocks mysql -P 9030 -h 127.0.0.1 -u root -e "SELECT count(*) FROM dw.stg_dim_oil_gas; SELECT count(*) FROM dw.stg_changes_oil_gas; SELECT count(*) FROM dw.stg_fact_oil_gas;"
```

## 5. ODS + SCD2

```powershell
docker exec sr-py python /app/scd2.py --domain oil-gas
docker exec starrocks mysql -P 9030 -h 127.0.0.1 -u root -e "SELECT id, status, valid_from, valid_to, is_current FROM dw.ods_dim_oil_gas WHERE id='W-01' ORDER BY valid_from;"
```

## 6. DM + исследование

```powershell
docker exec sr-py python /app/dm.py --domain oil-gas
docker exec sr-py python /app/research.py --domain oil-gas
```

## 7. MCP-сервер — проверка `run_sql`

```powershell
docker exec sr-py python /app/mcp_check.py --domain oil-gas
```

Должно напечатать `инструменты: ['run_sql']`, затем `SELECT count(*):` с числом, а последняя строка —
`DROP (должен быть ERROR): ERROR: разрешён только read-only (SELECT / SHOW / DESCRIBE / EXPLAIN)`.

## 8. Агент — эмбеддинги + 4 режима

```powershell
docker exec sr-py python /app/embed.py --domain oil-gas
```

```powershell
docker exec sr-py python /app/agent.py --domain oil-gas --mode none "Сколько добыла скважина W-02 в январе 2026?"
docker exec sr-py python /app/agent.py --domain oil-gas --mode sql  "Сколько добыла скважина W-02 в январе 2026?"
docker exec sr-py python /app/agent.py --domain oil-gas --mode docs "Сколько добыла скважина W-02 в январе 2026?"
docker exec sr-py python /app/agent.py --domain oil-gas --mode both "Сколько добыла скважина W-02 в январе 2026?"
```

> Если `sql`-режим отдаёт странный SQL — это предел модели `qwen2.5:1.5b` (норма). Подскажи таблицу
> в вопросе или возьми `qwen2.5:3b` (см. `guide.md` §11).

## 9. Заполни отчёт

Ответь на 6 вопросов заключения (`guide.md` §10) и оформи отчёт по образцу из гайда (§9).

## 10. Удалить всё

```powershell
docker rm -f sr-py starrocks qdrant ollama
docker volume rm ollama-models hf-cache
docker rmi starrocks/allin1-ubuntu:3.3.11 qdrant/qdrant:latest ollama/ollama:latest python:3.12-slim
cd ..
Remove-Item -Recurse -Force лаба-4.5-starrocks-agent
```

> Volume/образы лабы 4 (`rag-4_*`) не трогай — они от другой лабы.
