# Гайд 6.5. StarRocks DWH (STG → ODS → DM + SCD2) и агент данных (MCP × RAG)

Стек: **StarRocks (данные, три слоя) + Qdrant (документы) + Ollama (LLM) + python (скрипты и MCP-сервер)** — всё поднимается здесь, в этой лабе.

Это продолжение курса. В лабах 1–3 мы строили Lakehouse (MinIO + Lakekeeper + Trino + Iceberg), в гайде 6 — RAG-ретривер поверх него, в лабе 4 (4 часа) — полный RAG с генерацией. Здесь мы **меняем движок на StarRocks**, строим классический DWH-слой **STG → ODS → DM** с **SCD2** по одной из **4 областей**, а поверх — **агента**, который ходит SQL-ем в StarRocks (через **MCP**) и векторами в документы (через RAG). Заканчивается письменным **заключением**.

> ⏱️ План — **~3 часа**. Самые долгие механические этапы — первый `pip install` (torch) и первый запуск MCP/агента. Начинай ставить зависимости сразу после §2, чтобы они доскачались, пока ты делаешь STG/ODS.

---

## 0. Что мы строим и зачем

**StarRocks** — это MPP-аналитическая СУБД: в отличие от Trino (который только *запрашивает* данные, лежащие в MinIO как parquet), StarRocks **хранит данные сама**, в своём колоночном формате. Iceberg ей не нужен.

Три слоя — это классическая схема хранилища данных:

| Слой | Что там | Модель таблицы StarRocks | Аналог из лаб 1–3 |
|---|---|---|---|
| **STG** (staging) | сырые данные как есть | `DUPLICATE KEY` | Bronze |
| **ODS** (operational) | очищенные + **SCD2-история** измерений + факт | dim: `PRIMARY KEY(sk)`, факт: `DUPLICATE KEY` | Silver |
| **DM** (data mart) | витрины-агрегаты | `AGGREGATE KEY` | Gold |

**SCD2** (Slowly Changing Dimension Type 2) — способ хранить *историю* изменений измерения: каждая версия строки — отдельная строка с `valid_from`/`valid_to`/`is_current`. Это позволяет ответить и «как сейчас», и «как было на дату X».

**MCP** (Model Context Protocol) — открытый протокол, по которому LLM-агент вызывает *инструменты*. Мы сделаем инструмент `run_sql`, через который агент читает StarRocks, — вместо того чтобы «вшивать» SQL прямо в код агента.

Схема:

```
                 ┌────────────────────────────┐
                 │  агент (agent.py, python)   │
                 │  LLM-роутер: sql / docs / both│
                 └───────┬──────────────┬───────┘
            MCP (stdio)   │              │  ANN (Qdrant)
        ┌────────────────▼────┐   ┌──────▼──────┐
        │ mcp_server.py       │   │  документы   │
        │ run_sql(SELECT/… )  │   │  (свой корпус)    │
        └────────┬────────────┘   └─────────────┘
                 │ MySQL-протокол :9030
        ┌────────▼────────────┐
        │   StarRocks          │
        │   dw.stg_* / ods_* / dm_*  │
        └─────────────────────┘
```

### Мост к лабам 1–4

| Кусок этой лабы | Из какой лабы | Что меняется |
|---|---|---|
| SCD2 «закрыть старую + вставить новую» | лаба 3 (Spark `MERGE`) | в StarRocks нет `MERGE` → два оператора `UPDATE` + `INSERT` |
| STG/ODS/DM | лабы 1–3 (медальон Bronze/Silver/Gold) | та же идея, другое имя слоёв |
| Qdrant + эмбеддинги + документы | (поднимаем здесь) | свой корпус `documents/`, `embed.py` → `chunks_500` |
| LLM (Ollama qwen2.5) | (поднимаем здесь) | `docker run` + `ollama pull` |
| 🆕 MCP-инструмент `run_sql` | — | новое: агент читает БД через протокол |

> 💡 **Почему без Iceberg.** На Iceberg-таблицах StarRocks не умеет `UPDATE`/`DELETE`/`MERGE` (только `INSERT`), а SCD2 без «закрыть старую версию» не сделать. Поэтому используем **нативные** таблицы StarRocks, а Iceberg в этой лабе не нужен.

---

## 0.5. План на ~3 часа

| Время | Блок | Что делаем | Чек-пойнт |
|---|---|---|---|
| 0:00–0:15 | Разминка | StarRocks поднят, `SHOW DATABASES` отвечает | подключение работает |
| 0:15–0:30 | python-контейнер | `docker run sr-py`, `pip install` (torch CPU + mcp + …) | `import pymysql` работает |
| 0:30–0:55 | STG | `loader.py` грузит CSV своей области в `stg_*` | число строк совпало |
| 0:55–1:30 | ODS + SCD2 | `scd2.py`: dim со SCD2 + факт; проверка истории «как было на дату» | у изменённого `id` >1 версии |
| 1:30–2:00 | DM + исследование | `dm.py`: агрегат по месяцам; `research.py`: Duplicate vs Aggregate vs Primary | таблица «модель ↔ строки/время» |
| 2:00–2:20 | MCP-сервер | `mcp_server.py` + проверка инструмента `run_sql` | `run_sql` вернул число |
| 2:20–2:50 | Агент | `agent.py`: 4 режима `none/sql/docs/both` | ответы + таблица §9 |
| 2:50–3:00 | Заключение | письменно ответить на 6 вопросов §10 | лаба сдана |

---

## 1. Пререквизиты

**1.1. Docker Desktop** — запущен (гайд 1, §1).

**1.2. Лаба самодостаточна.** Всё нужное — StarRocks, Qdrant, Ollama, документы и эмбеддинги — поднимается и готовится прямо здесь, поэтапно. Никаких других лаб делать не нужно.

**1.3. Свободные порты.** Мы займём `9030` (StarRocks), `6333` (Qdrant), `11434` (Ollama). Проверь все три:

```bash
lsof -nP -iTCP:9030 -iTCP:6333 -iTCP:11434 -sTCP:LISTEN
```

Пустой вывод = всё свободно.

**1.4. Рабочая папка.** Все файлы лабы лежат в папке `лаба-4.5-starrocks-agent/` (там, где этот гайд). Скрипты — `loader.py`, `scd2.py`, `dm.py`, `research.py`, `embed.py`, `mcp_server.py`, `agent.py`; данные — в `data/<область>/`, документы для RAG — в `documents/<область>/`. Дальше все команды с `$(pwd)` выполняются **из этой папки**.

---

## 2. Шпаргалка: порты и пароли

| Сервис | Адрес | Логин | Пароль |
|---|---|---|---|
| **StarRocks** — MySQL-протокол | localhost:9030 | `root` | пусто |
| **Qdrant** — векторная БД | localhost:6333 | — | — |
| **Ollama** — локальный LLM | localhost:11434 | — | — |

> Пароль root пустой — это демо-режим, только для локальной лабы. База называется `dw`.

---

## 3. Этап 1 — StarRocks

Запускаем one-node образ (один контейнер = 1 FE + 1 BE):

```bash
docker run -d --name starrocks \
  -p 9030:9030 -p 8030:8030 -p 8040:8040 \
  starrocks/allin1-ubuntu:3.3.11
```

> `-d` — в фоне; `-p 9030:9030` — проброс MySQL-порта FE (для SQL), `8030/8040` — HTTP-порты (для загрузки/мониторинга).

**Дождись готовности** (~30–60 с) и подключись клиентом `mysql`, который уже есть внутри образа:

```bash
docker exec -it starrocks mysql -P 9030 -h 127.0.0.1 -u root
```

Внутри выполни:

```sql
SHOW DATABASES;
CREATE DATABASE dw;
USE dw;
```

Должны увидеть `dw` в списке. Выйти из клиента — `exit` (или `\q`).

> 💡 **DBeaver вместо `mysql`-клиента** (кому удобнее): Database → New Connection → **MySQL** → Host `localhost`, Port `9030`, User `root`, пароль пустой. Дальше все SQL можно выполнять и там.

**Чек-пойнт:** `SHOW DATABASES` вернул `dw`.

---

## 4. Этап 2 — python-контейнер и зависимости

Поднимаем python-контейнер (висит в фоне, все джобы запускаем через `docker exec` — как в гайде 6):

```bash
docker run -d --name sr-py \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd):/app" \
  -v hf-cache:/root/.cache/huggingface \
  python:3.12-slim \
  sleep infinity
```

> ⚠️ `--add-host host.docker.internal:host-gateway` обязателен: из этого контейнера мы ходим на хост к StarRocks (`host.docker.internal:9030`), Qdrant и Ollama. Без него — `Connection refused`.

Ставим зависимости (torch — CPU-сборкой, чтобы не тянуть CUDA ~2 ГБ):

```bash
docker exec sr-py sh -c "pip install -q torch --index-url https://download.pytorch.org/whl/cpu"
docker exec sr-py sh -c "pip install -q -r /app/requirements.txt"
```

`requirements.txt` ставит: `pymysql` (клиент к StarRocks), `qdrant-client` + `sentence-transformers` (эмбеддинги/RAG), `requests` (Ollama), `mcp` (MCP-сервер и клиент).

**Чек-пойнт:**

```bash
docker exec sr-py python -c "import pymysql, qdrant_client, sentence_transformers, mcp, requests; print('ok')"
```

Должно напечатать `ok`.

---

## 4.5. Этап 2.5 — Qdrant и Ollama (RAG-часть)

Агенту нужны документы (их эмбеддинги лежат в Qdrant) и локальная LLM (Ollama). Поднимаем оба здесь.

**Qdrant** — векторная БД для эмбеддингов:

```bash
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant:latest
```

**Ollama** — движок локальных LLM. Модель храним в volume `ollama-models`, чтобы не качать заново:

```bash
docker run -d --name ollama -p 11434:11434 -v ollama-models:/root/.ollama ollama/ollama:latest
```

**Модель** `qwen2.5:1.5b` (~1 ГБ). Загрузи её (команда ниже заблокирует терминал на несколько минут — можно запустить в отдельном терминале заранее, пока делаешь часть A):

```bash
docker exec ollama ollama pull qwen2.5:1.5b
```

> ⚠️ Не используй `docker exec -d …` (фоновый запуск внутри контейнера): такой процесс может тихо оборваться, и модель не появится в списке. Запускай pull обычным (foreground) способом.

**Чек-пойнт** (когда модель доскачается):

```bash
curl -s http://localhost:6333/collections     # Qdrant отвечает (пока пусто)
docker exec ollama ollama list                # в списке появился qwen2.5:1.5b
```

> Порядок неважен: Qdrant/Ollama можно поднять и раньше (§3). Главное — запустить `ollama pull` пораньше, чтобы не ждать гигабайт в самом конце.

---

## 5. Этап 3 — STG: загрузка сырых данных

Данные четырёх областей лежат в `data/`. **Выбери свою область**:

| Вариант | Область | `--domain` | Что это |
|---|---|---|---|
| 1 | Нефтегаз | `oil-gas` | скважины (статус/оператор), факт «добыча» |
| 2 | Металлургия | `metallurgy` | печи (статус/бригада), факт «выплавка» |
| 3 | Химтех | `chemical` | реакторы (статус/технолог), факт «выход партии» |
| 4 | ИВТ | `cs` | серверы (статус/команда), факт «запросы» |

В папке каждой области три CSV (столбцы у всех областей одинаковые, различаются значения):

- `dim.csv` — измерение на старте: `id,name,status,owner`;
- `changes.csv` — изменения (то, что должно попасть в историю через SCD2): `id,status,owner`;
- `fact.csv` — факты-события: `id,date,value`.

`owner` = «ответственный» (для скважины — оператор, для печи — бригада, для реактора — технолог, для сервера — команда).

Запусти загрузчик (подставь свою область):

```bash
docker exec sr-py python /app/loader.py --domain oil-gas
```

Что делает `loader.py`: создаёт в `dw` три STG-таблицы (`stg_dim_*`, `stg_changes_*`, `stg_fact_*`) с моделью `DUPLICATE KEY` (хранит всё как есть) и грузит в них CSV через `pymysql`.

**Чек-пойнт** — сверь число строк (в клиенте или DBeaver):

```sql
SELECT count(*) FROM dw.stg_dim_oil_gas;      -- == числу строк в data/oil-gas/dim.csv
SELECT count(*) FROM dw.stg_fact_oil_gas;     -- == числу строк в fact.csv
SELECT count(*) FROM dw.stg_changes_oil_gas;  -- == числу строк в changes.csv
```

> Если взял другую область — подставь её суффикс вместо `oil_gas` (дефис в имени папки заменяется на `_`).

---

## 6. Этап 4 — ODS: SCD2 (история измерений)

Запусти джобу ODS:

```bash
docker exec sr-py python /app/scd2.py --domain oil-gas
```

Что делает `scd2.py` (прочитай в файле):

1. Создаёт **SCD2-измерение** `ods_dim_*`: `PRIMARY KEY(sk)` (суррогатный ключ — каждая версия отдельной строкой) + колонки `valid_from`, `valid_to`, `is_current`.
2. Грузит в него **начальный снапшот** из `stg_dim_*` (все строки `is_current = TRUE`).
3. Применяет **изменения** из `stg_changes_*`: для каждой изменившейся строки — `UPDATE` (закрыть старую версию) + `INSERT` (вставить новую).
4. Переносит факт в `ods_fact_*`.

### SCD2 без MERGE — как это устроено

В StarRocks нет `MERGE INTO`, поэтому «закрыть + вставить» — два явных оператора (это и делает `scd2.py`):

```sql
-- закрыть текущую версию
UPDATE dw.ods_dim_oil_gas
   SET valid_to = DATE '2026-03-01', is_current = FALSE
 WHERE id = 'W-01' AND is_current = TRUE;

-- вставить новую версию (новый sk)
INSERT INTO dw.ods_dim_oil_gas (sk, id, name, status, owner, valid_from, valid_to, is_current)
VALUES (6, 'W-01', 'Скважина Северная', 'добыча', 'Оператор А',
        DATE '2026-03-01', NULL, TRUE);
```

### Проверь историю: «как было на дату X»

Главная ценность SCD2 — ответить не только «как сейчас», но и «как было на дату X». Изменение мы применили `2026-03-01`; спросим, какой статус был у `W-01` `2026-02-15`:

```sql
SELECT status FROM dw.ods_dim_oil_gas
 WHERE id = 'W-01'
   AND valid_from <= DATE '2026-02-15'
   AND (valid_to > DATE '2026-02-15' OR valid_to IS NULL);
```

Сравни с текущим статусом (`WHERE is_current = TRUE`) — увидишь, что SCD2 хранит **обе** версии: старую (с `valid_to`) и новую (с `is_current`). Это и есть «память с историей», которую потом использует агент.

**Чек-пойнт:** у изменённого объекта (`W-01`) в `ods_dim_*` больше одной строки; `SELECT ... WHERE is_current = TRUE` возвращает ровно одну строку на каждый `id`.

---

## 7. Этап 5 — DM: витрина-агрегат

Запусти:

```bash
docker exec sr-py python /app/dm.py --domain oil-gas
```

`dm.py` строит витрину `dm_agg_*` с моделью `AGGREGATE KEY`: при загрузке строки с одинаковым ключом **суммируются** (это и есть «пред-агрегация», ради неё слой DM и нужен). Вот что он выполняет (эквивалент SQL):

```sql
CREATE TABLE dw.dm_agg_oil_gas (
    id   VARCHAR(20),
    ym   VARCHAR(7),
    total_value DOUBLE SUM
) AGGREGATE KEY(id, ym) DISTRIBUTED BY HASH(id) BUCKETS 1;

INSERT INTO dw.dm_agg_oil_gas
SELECT id, date_format(date, '%Y-%m') AS ym, value
FROM dw.ods_fact_oil_gas;
```

**Чек-пойнт** — аналитический вопрос: «сколько всего по каждому объекту за январь 2026?»

```sql
SELECT id, total_value FROM dw.dm_agg_oil_gas WHERE ym = '2026-01';
```

И соединим витрину с *текущим* состоянием измерения:

```sql
SELECT m.id, d.status, m.total_value
FROM dw.dm_agg_oil_gas m
JOIN dw.ods_dim_oil_gas d ON d.id = m.id AND d.is_current = TRUE
WHERE m.ym = '2026-01';
```

### Исследование: модель таблицы (Duplicate vs Aggregate vs Primary Key) — меняем модель, измеряем

Слой DM мы построили на `AGGREGATE KEY`. **Почему не `DUPLICATE KEY` или `PRIMARY KEY`?** Проверим на одном и том же наборе фактов: скрипт грузит их в три таблицы с разной моделью и меряет строки и время запроса «сумма по объекту».

```bash
docker exec sr-py python /app/research.py --domain oil-gas
```

Что делает `research.py`: читает `fact.csv`, **размножает** строки повтором (`--repeat`, по умолчанию 5000) и грузит один и тот же объём в три таблицы:

- `r_dup` — `DUPLICATE KEY(id, date)`: хранит **всё как есть**;
- `r_pk` — `PRIMARY KEY(id, date)`: **дедуплицирует** по ключу (повтор `(id,date)` перезаписывается) и умеет upsert;
- `r_agg` — `AGGREGATE KEY(id)` с `value SUM`: **пред-агрегирует** — складывает значение по `id` уже при загрузке.

Заполни таблицу вывода `research.py` (это твой **первый** результат):

| Модель | строк | время запроса (мс) | Что делает с данными |
|---|---|---|---|
| `r_dup` — Duplicate | | | хранит всё, детально, но много |
| `r_pk` — Primary Key | | | дедуп по ключу + upsert |
| `r_agg` — Aggregate | | | пред-сумма по id: мало и быстро, но теряет события |

> ⏱️ На маленьком `--repeat` разрыв по времени может быть едва заметен — увеличь (`--repeat 20000`). По **строкам** разрыв виден всегда.

**Вывод** (одной фразой): это объясняет выбор моделей по слоям — STG и факт хранят сырое (`DUPLICATE`), dim-SCD2 требует upsert по ключу (`PRIMARY`), а витрина DM пред-агрегирует (`AGGREGATE`): меньше место и быстрее агрегаты, но ценой детализации.

---

## 8. Этап 6 — MCP-сервер: инструмент `run_sql`

Теперь «мост» между LLM и StarRocks. `mcp_server.py` — это **MCP-сервер**, который отдаёт агенту один инструмент `run_sql(sql)`. Загляни в файл: внутри `pymysql` ходит в StarRocks и **разрешает только чтение** (`SELECT`/`SHOW`/`DESCRIBE`/`EXPLAIN` — всё остальное отклоняется).

Проверим инструмент напрямую (маленький клиент — файл `mcp_check.py`):

```bash
docker exec sr-py python /app/mcp_check.py --domain oil-gas
```

**Чек-пойнт:** печатается `инструменты: ['run_sql']`, затем `SELECT count(*):` с числом, а последняя строка
`DROP (должен быть ERROR): ERROR: разрешён только read-only (SELECT / SHOW / DESCRIBE / EXPLAIN)` — и таблица
на месте. Это и есть **read-only-контракт** инструмента.

---

## 9. Этап 7 — агент: 4 режима

`agent.py` — агент, который сам решает, куда идти за ответом: в StarRocks (через MCP `run_sql`) или в документы (через Qdrant), или в оба. Сначала агент узнаёт схему БД своей области (вызывая `SHOW TABLES` + `DESCRIBE` через MCP и оставляя только таблицы цепочки `stg_*/ods_*/dm_*` с суффиксом `_<область>`, без служебных `r_*` из `research.py`), потом пишет SQL и отвечает.

**Сначала — эмбеддинги документов своей области.** Агент ищет по коллекции Qdrant `chunks_500`; наполни её из папки `documents/<область>/` (подставь свою область):

```bash
docker exec sr-py python /app/embed.py --domain oil-gas
```

> Первый запуск качает модель MiniLM (~470 МБ) в кэш `hf-cache` — разово. Проверь: `curl -s http://localhost:6333/collections/chunks_500` должен вернуть JSON со счётчиком `points_count`.

Четыре режима (`--mode`):

| Режим | Доступные инструменты | Что демонстрирует |
|---|---|---|
| `none` | — | чистая LLM без данных (галлюцинирует) |
| `sql` | только `run_sql` (StarRocks) | цифры из БД, но без методики из документов |
| `docs` | только поиск по документам (Qdrant) | методика, но без актуальных цифр |
| `both` | `run_sql` + документы | **симбиоз**: документ даёт методику, SQL — цифры |

Прогони один и тот же вопрос в четырёх режимах (подставь свою область; пример — нефтегаз):

```bash
docker exec sr-py python /app/agent.py --domain oil-gas --mode none "Сколько добыла скважина W-02 в январе 2026?"
docker exec sr-py python /app/agent.py --domain oil-gas --mode sql  "Сколько добыла скважина W-02 в январе 2026?"
docker exec sr-py python /app/agent.py --domain oil-gas --mode docs "Сколько добыла скважина W-02 в январе 2026?"
docker exec sr-py python /app/agent.py --domain oil-gas --mode both "Сколько добыла скважина W-02 в январе 2026?"
```

> ⏱️ Первый запуск каждого режима грузит модель эмбеддингов в память (~1–2 с). Комбо-вопрос «как считается X и сколько это в наших данных» лучше спрашивать в режиме `both`: документ даёт методику, SQL подставляет цифры.

### Исследование: 4 режима (измеримо — верно/нет)

Составь набор из 6 вопросов своей области (2 «факт из документов», 2 «цифра из БД», 2 «комбо») и прогони каждый в 4 режимах. Заполни таблицу — это твой второй результат:

| Вопрос | none | sql-only | docs-only | sql+docs |
|---|---|---|---|---|
| … | верно/нет + почему | верно/нет | верно/нет | верно/нет |
| … | | | | |

---

## 10. Заключение («Итоги и вопросы»)

Ответь письменно (это и есть итог лабы):

1. **StarRocks vs лабы 1–3.** Чем StarRocks отличается от стека MinIO+Lakekeeper+Trino? Что значит «данные внутри», и почему для SCD2 понадобились нативные таблицы (нет `UPDATE`/`DELETE`/`MERGE` на Iceberg)?
2. **Слои.** Как STG/ODS/DM соотносятся с Bronze/Silver/Gold? Почему SCD2 живёт именно в ODS, а не в STG или DM?
3. **SCD2 без MERGE.** Как «закрыть старую + вставить новую» реализуется в StarRocks? Зачем суррогатный ключ `sk` и что дают `valid_from`/`valid_to`/`is_current`? Покажи запрос «как было на дату X».
4. **Модель таблицы.** Что показало исследование Duplicate/Aggregate/Primary (строки, время)? Почему STG и факт — `DUPLICATE KEY`, dim-SCD2 — `PRIMARY KEY`, а DM — `AGGREGATE KEY`? Какой компромисс между детализацией и размером/скоростью ты измерил?
5. **Симбиоз.** По каким признакам агент идёт в SQL, а по каким в документы? Что даёт MCP против «SQL, вшитого в код агента»? Приведи вопрос, где ответ зависит от времени (SCD2-история).
6. **Галлюцинации.** На своём прогоне: что ответила модель без инструментов / только SQL / только RAG / SQL+RAG, и где именно провалился каждый режим?

---

## 11. Если что-то пошло не так

- **StarRocks долго стартует / `mysql` не отвечает** — подожди до 60 с: `docker logs starrocks`. Признак готовности — строка про `FE`/`BE` в статусе `OK`.
- **`docker exec sr-py … Connection refused`** — контейнер `sr-py` создан без `--add-host host.docker.internal:host-gateway`. Пересоздай его с этим флагом (см. §4).
- **`pip install torch` виснет** — PyPI отдаёт CUDA-сборку (~2 ГБ). Ставь CPU-сборку отдельно (см. §4).
- **`ImportError: No module named 'mcp'`** — не доставлен пакет. Повтори `pip install -r /app/requirements.txt` и проверь чек-пойнт §4.
- **`Table 'dw.stg_...' doesn't exist`** — сначала `loader.py`, потом `scd2.py`/`dm.py`. Проверь `SHOW TABLES FROM dw`.
- **`UPDATE`/`DELETE` не сработали** — они работают только на `PRIMARY KEY` таблицах (dim), а не на `DUPLICATE KEY` (STG/факт). Сверь модель таблицы (`SHOW CREATE TABLE dw.<таблица>`).
- **Агент пишет странный SQL** — модель маленькая (`qwen2.5:1.5b`), SQL может быть неточным. Дай ей подсказку в вопросе (название таблицы) или попробуй `qwen2.5:3b` (нужно ~3 ГБ RAM). Это нормально — цель не идеальный SQL, а продемонстрировать, как агент ходит по инструментам. Напоминание: у `agent.py` обязательно указывать `--domain <область>`, иначе он не найдёт таблицы и упадёт.
- **Агент пишет SQL про `r_dup`/`r_pk`/`r_agg`** — это служебные таблицы исследования моделей (`research.py`). Агент их не видит: `discover_schema` берёт только таблицы с суффиксом `_<область>`. Если видишь их в SQL — проверь, что передал `--domain`.
- **`run_sql` вернул `(пусто)` или `ERROR`** — запрос не прошёл. Проверь его вручную в `mysql`-клиенте (§3) и сверь имена таблиц.
- **`ImportError: No module named 'mcp.server.fastmcp'`** — поставился `mcp` версии 2.x, где `FastMCP` переименован в `MCPServer`. Наша лаба написана под v1 API, поэтому пинируем: `docker exec sr-py pip install "mcp<2"`. В `requirements.txt` это уже учтено.
- **Агент отвечает «не знаю» по всем вопросам** — не построены эмбеддинги. Выполни `docker exec sr-py python /app/embed.py --domain <область>` и проверь `curl -s http://localhost:6333/collections/chunks_500`.
- **`ollama pull` долго / модели нет** — модель ~1 ГБ, качается разово. Проверь `docker exec ollama ollama list` и логи `docker logs ollama`.

---

## 12. В конце — удалить всё

После сдачи освободи место: убери контейнеры, их данные (volume), образы и папку лабы.
Всё, что ниже, создала только эта лаба — у лабы 4 свои volume `rag-4_*`, их **не трогаем**.

```bash
# 1) остановить и удалить 4 контейнера
docker rm -f sr-py starrocks qdrant ollama

# 2) удалить данные: модель Ollama + кэш эмбеддингов MiniLM
docker volume rm ollama-models hf-cache

# 3) удалить образы (при следующем запуске скачаются заново, ~16 ГБ)
docker rmi starrocks/allin1-ubuntu:3.3.11 qdrant/qdrant:latest \
           ollama/ollama:latest python:3.12-slim

# 4) удалить папку лабы — выйди из неё (подставь своё имя, если переименовал)
cd .. && rm -rf лаба-4.5-starrocks-agent
```

Проверь, что ничего не осталось:

```bash
docker ps -a --filter "name=sr-py|name=starrocks|name=qdrant|name=ollama"   # пусто
docker volume ls | grep -E "ollama-models|hf-cache"                          # пусто
docker images | grep -E "starrocks|ollama/ollama|qdrant/qdrant"             # пусто
```

> Чтобы лишь **приостановить** (не удалять) — `docker stop sr-py starrocks qdrant ollama` без `rm`.
> Опционально можно добить кэш сборки `docker system prune`, но без `-a`: с `-a` он удалит
> и образы **других** проектов на машине.
