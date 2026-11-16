"""Проверка интерфейса Streamlit с реальным поиском и генерацией."""
import json
from pathlib import Path
from streamlit.testing.v1 import AppTest

app=AppTest.from_file('/app/03-вопрос-ответ-ui/app.py').run(timeout=120)
assert not app.exception
assert app.selectbox[0].value == 500
assert app.checkbox[0].value is True
app.text_input[0].set_value('Что такое химическая технология?')
app.button[0].click().run(timeout=120)
assert not app.exception
text='\n'.join(str(x.value) for x in app.markdown)
assert 'прикладная' in text.lower()
assert 'doc_id=1' in text
Path('/app/evidence/ui_check.json').write_text(json.dumps({'status':'PASS','question':'Что такое химическая технология?','chunk_size':500,'rag':True,'answer_and_chunks':text},ensure_ascii=False,indent=2),encoding='utf-8')
print('UI PASS')
