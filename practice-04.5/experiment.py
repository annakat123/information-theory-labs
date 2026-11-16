"""Сохраняет ответы и обращения агента к источникам для шести вопросов."""
import asyncio
import json
from pathlib import Path
import agent

QUESTIONS = [
 ('doc1','На какие пять групп делят процессы химической технологии по кинетическим закономерностям?'),
 ('doc2','Что такое ректификация и за счёт какого взаимодействия она разделяет смеси?'),
 ('sql1','Каков выпуск реактора R-02 за январь 2026 года?'),
 ('sql2','Кто отвечал за реактор R-01 на 15 января 2026 года и кто на 15 марта 2026 года?'),
 ('both1','Что такое полимеризация и каков суммарный выпуск реактора R-03 за январь и февраль 2026 года?'),
 ('both2','Назови пять групп процессов химической технологии и суммарный выпуск реактора R-01 за январь-март 2026 года.'),
]

async def main():
    out=Path('/app/evidence')
    out.mkdir(exist_ok=True)
    for qid, question in QUESTIONS:
        for mode in ('none','sql','docs','both'):
            target=out / f'{qid}_{mode}.json'
            if target.exists(): continue
            trace=[]
            original_sql=agent.run_sql_via_mcp
            original_docs=agent.search_docs
            async def traced_sql(sql):
                result=await original_sql(sql)
                trace.append({'tool':'run_sql','sql':sql,'result':result})
                return result
            def traced_docs(q,k=3):
                result=original_docs(q,k)
                trace.append({'tool':'search_docs','question':q,'context':result})
                return result
            agent.run_sql_via_mcp=traced_sql
            agent.search_docs=traced_docs
            print(qid,mode,flush=True)
            try:
                answer=await agent.answer(question,mode,'chemical')
                result={'question':question,'mode':mode,'answer':answer,'trace':trace}
            except Exception as exc:
                result={'question':question,'mode':mode,'error':str(exc),'trace':trace}
            finally:
                agent.run_sql_via_mcp=original_sql
                agent.search_docs=original_docs
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': asyncio.run(main())
