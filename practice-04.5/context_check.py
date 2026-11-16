"""Отдельная проверка: подсказка с таблицей и узкая схема вместо всего DWH."""
import asyncio
import json
from pathlib import Path
import agent

async def main():
    questions=[('numeric','sql','Каков выпуск реактора R-02 за январь 2026 года? Для числа используй только dw.ods_fact_chemical: id - код реактора, date - дата, value - выпуск. Фильтр id равен R-02, период с 2026-01-01 до 2026-02-01, сумма value. Не используй JOIN.'),
               ('combo','both','Назови пять групп процессов химической технологии по кинетическим закономерностям. Затем укажи суммарный выпуск R-01 за январь-март 2026 года. Для числа используй только dw.ods_fact_chemical: id - код реактора, date - дата, value - выпуск. Фильтр id равен R-01, период с 2026-01-01 до 2026-04-01, сумма value. Не используй JOIN.')]
    for label,mode,question in questions:
        trace=[]
        original_sql=agent.run_sql_via_mcp
        original_discovery=agent.discover_schema
        original_docs=agent.search_docs
        async def sql(q):
            result=await original_sql(q)
            trace.append({'sql':q,'result':result})
            return result
        async def discovery(domain):
            full=await original_discovery(domain)
            return '\n'.join(line for line in full.splitlines() if line.startswith('ods_fact_chemical('))
        def docs(q,k=3):
            result=original_docs(q,k)
            trace.append({'context':result})
            return result
        agent.run_sql_via_mcp=sql
        agent.discover_schema=discovery
        agent.search_docs=docs
        print(label,flush=True)
        try:
            answer=await agent.answer(question,mode,'chemical')
            result={'question':question,'mode':mode,'answer':answer,'trace':trace}
        except Exception as exc:
            result={'question':question,'mode':mode,'error':str(exc),'trace':trace}
        finally:
            agent.run_sql_via_mcp=original_sql
            agent.discover_schema=original_discovery
            agent.search_docs=original_docs
        Path('/app/evidence/context_'+label+'.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': asyncio.run(main())
