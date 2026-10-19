import json
from types import SimpleNamespace

import pytest

from paper_agent.graph.route import classify_intent
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.services import assistant as service


def broken(*a, **kw):
    raise RuntimeError('controlled classification outage')


@pytest.mark.parametrize('text', ['解释 Query Expansion 的作用', 'SQL 查询是如何工作的', '这篇论文怎么用对比学习', '论文中的状态表示'])
def test_technical_questions_are_not_commands(text):
    assert classify_intent(text, chat_fn=broken)['intent'] == 'qa'


@pytest.mark.parametrize('text,operation', [
    ('当前库中的论文都是哪些研究方向上的？', 'topics'),
    ('Rag方向上有多少篇论文：', 'count'),
    ('解释库中论文所使用的方法', 'methods'),
])
def test_reported_catalogue_requests(text, operation):
    route = classify_intent(text, chat_fn=broken)
    assert route['intent'] == 'catalogue'
    assert route['catalogue']['operation'] == operation


def test_classification_receives_clarification_history():
    captured = []
    def fake(messages, **kw):
        captured.extend(messages)
        return json.dumps({'intent': 'catalogue', 'catalogue': {'operation': 'count', 'query': 'RAG'}}), {}
    route = classify_intent('对，就这个方向', history=[('RAG方向有多少论文？', '你是指库里的文献吗？')], chat_fn=fake)
    assert 'RAG' in json.dumps(captured, ensure_ascii=False)
    assert route['catalogue']['query'] == 'RAG'


def test_classifier_failure_visible():
    route = classify_intent('解释损失函数', chat_fn=broken)
    assert route['via'] == 'heuristic' and route['fallback_reason'] == 'RuntimeError'


def test_filtered_count_uses_metadata_not_total(tmp_path):
    lib = Library(tmp_path / 'library.db')
    try:
        for sid, title in [('local:a', 'Retrieval-Augmented Generation for Research'),
                           ('local:b', 'RAG Systems'), ('local:c', 'Image Fragmentation')]:
            lib.upsert_paper(Paper(source='local', source_id=sid, title=title))
        retriever = SimpleNamespace(store=SimpleNamespace(count=lambda: 0))
        reply = service.handle_message('RAG方向上有多少篇论文', lib=lib, retriever=retriever,
                                       params={}, cfg={}, chat_fn=broken, allow_ingest=False)
        assert reply.intent == 'catalogue'
        assert '2 篇' in reply.reply and '题录' in reply.reply
        assert 'Image Fragmentation' not in reply.reply
    finally:
        lib.close()


def test_identity_explains_assistant_and_current_model(tmp_path):
    lib = Library(tmp_path / 'library.db')
    try:
        reply = service.handle_message('你是谁', lib=lib, retriever=None, params={},
                                       cfg={'llm': {'model': 'configured-qwen'}}, chat_fn=broken, allow_ingest=False)
        assert '个人文献' in reply.reply and 'configured-qwen' in reply.reply
    finally:
        lib.close()


def test_metadata_count_honors_scope(tmp_path):
    lib = Library(tmp_path / 'library.db')
    try:
        for sid in ('local:a', 'local:b'):
            lib.upsert_paper(Paper(source='local', source_id=sid, title='RAG Systems'))
        reply = service.handle_message('RAG方向上有多少篇论文', lib=lib, retriever=None, params={},
                                       cfg={}, chat_fn=broken, allow_ingest=False, paper_ids=['local:a'])
        assert '1 篇' in reply.reply and '所选' in reply.reply
        assert 'local:b' not in reply.reply
    finally:
        lib.close()


def test_dashscope_thinking_parameter(monkeypatch):
    from paper_agent import llm
    captured = []
    def create(**kw):
        captured.append(kw)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content='ok', tool_calls=[]))])
    monkeypatch.setattr(llm, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm, 'resolve_endpoint', lambda raw: SimpleNamespace(base_url=raw['base_url'], model=raw['model'], api_key='fake'))
    llm.chat([{'role': 'user', 'content': 'hello'}], cfg={'llm': {'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen', 'runtime_api_key': 'fake'}}, thinking=False)
    assert captured[0]['extra_body'] == {'enable_thinking': False}


def test_methods_overview_uses_each_indexed_paper_and_sources(tmp_path):
    from paper_agent.rag.qa import Retriever
    from paper_agent.rag.vectorstore import Hit
    lib = Library(tmp_path / 'library.db')
    try:
        for sid in ('local:a', 'local:b', 'local:unindexed'):
            lib.upsert_paper(Paper(source='local', source_id=sid, title=sid))
            if sid != 'local:unindexed': lib.set_status(sid, 'indexed')
        corpus = [Hit(paper_id=sid, title=sid, section='Methods', page=1,
                      text='The method uses a graph neural network.', distance=0, chunk_id=sid+'::0')
                  for sid in ('local:a', 'local:b')]
        store = SimpleNamespace(count=lambda: 2, all_hits=lambda **kw: [h for h in corpus if h.paper_id in kw.get('paper_ids', [])])
        retriever = Retriever(store, cfg={'retrieval': {'mode': 'bm25'}})
        reply = service.handle_message('解释库中论文所使用的方法', lib=lib, retriever=retriever,
                                       params={}, cfg={}, allow_ingest=False,
                                       chat_fn=lambda *a, **k: ('两篇论文使用图神经网络 [1][2]。', {'prompt': 11, 'completion': 7}))
        assert reply.intent == 'catalogue'
        assert '2 篇已索引' in reply.reply and '其中 2 篇' in reply.reply
        assert {s['paper_id'] for s in reply.sources} == {'local:a', 'local:b'}
        assert reply.telemetry['call_count'] == 1
    finally:
        lib.close()


def test_single_ask_catalogue_without_index_and_selected_scope(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from paper_agent.server import routes
    from paper_agent.server.app import create_app
    cfg = {'data_dir': str(tmp_path / 'data'), 'sources': {}}
    lib = Library(tmp_path / 'data/library.db')
    for sid in ('local:a', 'local:b'):
        lib.upsert_paper(Paper(source='local', source_id=sid, title='RAG Systems'))
    lib.close()
    monkeypatch.setattr(routes, 'load_config', lambda: cfg)
    monkeypatch.setattr(routes, '_retriever', lambda: pytest.fail('metadata count does not need an index'))
    with TestClient(create_app()) as client:
        response = client.post('/api/ask', json={'question': 'RAG方向上有多少篇论文', 'paper_ids': ['local:a']})
    assert response.status_code == 200
    assert '1 篇' in response.json()['answer'] and '所选' in response.json()['answer']
