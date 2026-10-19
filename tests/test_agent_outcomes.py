"""Outcome, accounting and trace regressions; all provider replies are injected."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from paper_agent.agent.loop import AgentRunResult, AgentStep, run_agent_task
from paper_agent.agent.verification import library_snapshot, verify_outcome
from paper_agent.agent.eval_agent import run_agent_evalset, aggregate_results
from paper_agent.agent.executor import ToolExecutor
from paper_agent.library import Library
from paper_agent.llm import ChatResponse, ToolCall
from paper_agent.models import Paper
from paper_agent.observability import CallLedger, task_observation, observed_call, normalize_usage
from paper_agent.rag.qa import Retriever, answer_question
from paper_agent.rag.vectorstore import Hit
from paper_agent.tools import ToolResult


def test_fatal_tool_and_confident_answer_are_not_success(tmp_path, monkeypatch):
    lib = Library(tmp_path / 'lib.db')
    executor = ToolExecutor(lib=lib, cfg={})
    run = AgentRunResult(answer='已完成入库', steps=[AgentStep(1, 'add_papers', status='fatal')])
    monkeypatch.setattr('paper_agent.agent.eval_agent.run_agent_task', lambda *a, **k: run)
    case = {'id': 'false-positive', 'kind': 'multi_step', 'task': '入库',
            'must_call': ['add_papers'], 'expect_keywords': [['完成']],
            'acceptance': [{'kind': 'library_add', 'min_added': 1}]}
    result = run_agent_evalset([case], executor_factory=lambda: executor)[0]
    assert result.execution_finished and result.answer_ok
    assert result.completed is False and result.tool_ok is False
    assert result.verification['status'] == 'failed'
    assert aggregate_results([result])['completion_rate'] == 0
    lib.close()


def test_uncontracted_answer_is_unverified(tmp_path):
    lib = Library(tmp_path / 'lib.db')
    result = run_agent_task('done', executor=ToolExecutor(lib=lib, cfg={}),
                            chat_fn=lambda *a, **k: ChatResponse('完成'))
    assert result.verification['status'] == 'unverified'
    assert result.telemetry['unknown_usage_calls'] == 1
    lib.close()


def test_ledger_add_must_exist_and_match_tool_observation(tmp_path):
    lib = Library(tmp_path / 'lib.db')
    executor = ToolExecutor(lib=lib, cfg={})
    before = library_snapshot(executor)
    run = AgentRunResult('已保存', steps=[AgentStep(1, 'add_papers', status='ok', observation={'selected_ids': ['arxiv:a']})])
    checks = [{'kind': 'library_add', 'source_ids': ['arxiv:a']}]
    assert verify_outcome(run, before=before, after=before, checks=checks)['status'] == 'failed'
    lib.upsert_paper(Paper(source='arxiv', source_id='arxiv:b', title='Unrelated'))
    assert verify_outcome(run, before=before, after=library_snapshot(executor), checks=checks)['status'] == 'failed'
    lib.upsert_paper(Paper(source='arxiv', source_id='arxiv:a', title='Expected'))
    assert verify_outcome(run, before=before, after=library_snapshot(executor), checks=checks)['status'] == 'passed'
    assert verify_outcome(run, before=before, after=library_snapshot(executor), checks=[{'kind': 'no_library_mutation'}])['status'] == 'failed'
    lib.close()


def test_nested_calls_and_final_summary_accounted_once(tmp_path):
    class Executor:
        def specs_for(self): return []
        def execute(self, *a):
            observed_call(lambda: SimpleNamespace(usage={'prompt_tokens': 30, 'completion_tokens': 10}), model='nested')
            return ToolResult(tool='ask_library', status='ok', data={})
    def chat(messages, *, tools):
        if any(m['role'] == 'tool' for m in messages):
            return ChatResponse('收尾', usage={'prompt': 7, 'completion': 3})
        return ChatResponse(None, [ToolCall('c', 'ask_library', '{}')], {'prompt': 11, 'completion': 4})
    path = tmp_path / 'trace.jsonl'
    result = run_agent_task('ask', executor=Executor(), chat_fn=chat, max_steps=1, trace_path=path)
    assert result.usage == {'prompt': 48, 'completion': 17}
    assert result.telemetry['call_count'] == 3
    assert result.telemetry['calls'][-1]['stage'] == 'agent.summary'
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert len([r for r in records if r['type'] == 'call']) == 3
    assert records[-1]['telemetry']['call_count'] == 3


def test_nested_usage_stops_next_decision():
    class Executor:
        def specs_for(self): return []
        def execute(self, *a):
            observed_call(lambda: SimpleNamespace(usage={'prompt': 100, 'completion': 20}))
            return ToolResult(tool='ask_library', status='ok', data={})
    count = []
    def chat(messages, *, tools):
        count.append(1)
        return (ChatResponse('summary', usage={'prompt': 2, 'completion': 1}) if len(count) > 1
                else ChatResponse(None, [ToolCall('c', 'ask_library', '{}')], {'prompt': 2, 'completion': 1}))
    result = run_agent_task('ask', executor=Executor(), chat_fn=chat, token_budget=50)
    assert result.stopped_reason == 'token_budget'
    assert len(count) == 2 and len(result.steps) == 1
    assert result.usage['prompt'] == 104


def test_qa_normalized_usage_retained():
    hit = Hit(paper_id='p', title='Fixture', section='Results', page=1, chunk_id='c', text='Recall@5 is 0.72', distance=0)
    retriever = Retriever(SimpleNamespace(all_hits=lambda **k: [hit]), cfg={'retrieval': {'mode': 'bm25'}})
    trace = {}
    answer_question('Recall@5', retriever=retriever, hits=[hit], trace=trace,
                    chat_fn=lambda *a, **k: ('Recall@5 is 0.72 [1]', {'prompt': 11, 'completion': 7}))
    assert trace['calls'][0]['usage'] == {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}


def test_usage_unknown_error_and_context_isolation():
    def task(n):
        ledger = CallLedger()
        with task_observation(ledger):
            observed_call(lambda: ChatResponse('ok', usage={'prompt': n, 'completion': 1}))
            observed_call(lambda: SimpleNamespace(usage={}), kind='rerank', units={'documents': 2})
            try:
                observed_call(lambda: (_ for _ in ()).throw(RuntimeError('do not log secrets')))
            except RuntimeError: pass
        return ledger.summary()
    with ThreadPoolExecutor(2) as pool:
        summaries = list(pool.map(task, [11, 22]))
    assert [s['prompt'] for s in summaries] == [11, 22]
    assert all(s['call_count'] == 3 and s['unknown_usage_calls'] == 2 and s['failed_calls'] == 1 for s in summaries)
    assert 'do not log secrets' not in json.dumps(summaries)
    assert normalize_usage({'prompt': True, 'completion': -1}) == {}


def test_evidence_contract_recomputes_citations(tmp_path):
    lib = Library(tmp_path / 'lib.db')
    lib.upsert_paper(Paper(source='local', source_id='local:a', title='Fixture'))
    state = library_snapshot(ToolExecutor(lib=lib, cfg={}))
    observation = {'answer_status': 'generated', 'answer': '指标为 0.72 [99]',
                   'citation_check': {'status': 'passed'},
                   'sources': [{'paper_id': 'local:a', 'chunks': [{'n': 1, 'text': 'Recall@5 is 0.72'}]}]}
    run = AgentRunResult('完成', steps=[AgentStep(1, 'ask_library', status='ok', observation=observation)])
    checks = [{'kind': 'evidence_answer'}]
    assert verify_outcome(run, before=state, after=state, checks=checks)['status'] == 'failed'
    observation['answer'] = '指标为 0.72 [1]'
    assert verify_outcome(run, before=state, after=state, checks=checks)['status'] == 'passed'
    observation['sources'][0]['paper_id'] = 'missing'
    assert verify_outcome(run, before=state, after=state, checks=checks)['status'] == 'failed'
    lib.close()


def test_standalone_qa_has_its_own_call_ledger():
    hit = Hit(paper_id='p', title='Fixture', section='Results', page=1, chunk_id='c', text='Recall@5 is 0.72', distance=0)
    retriever = Retriever(SimpleNamespace(all_hits=lambda **k: [hit]), cfg={'retrieval': {'mode': 'bm25'}})
    trace = {}
    answer_question('Recall@5', retriever=retriever, hits=[hit], trace=trace,
                    chat_fn=lambda *a, **k: ('Recall@5 is 0.72 [1]', {'prompt': 11, 'completion': 7}))
    assert trace['telemetry']['call_count'] == 1
    assert trace['telemetry']['prompt'] == 11
    assert trace['telemetry']['calls'][0]['stage'] == 'qa.answer'


def test_interrupted_history_retains_calls(tmp_path):
    from paper_agent.server.routes import _history_task
    trace = tmp_path / '20261016-120000-interrupted.jsonl'
    records = [{'type': 'task', 'task': 'not finished'}, {'type': 'call', 'id': 1,
                'kind': 'chat', 'stage': 'agent.decision', 'model': 'fixture', 'status': 'ok',
                'usage': {'prompt': 7, 'completion': 3, 'total': 10},
                'usage_known': True, 'elapsed_ms': 100, 'units': {}}]
    trace.write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')
    data = _history_task(trace, with_steps=True)
    assert data['status'] == 'error'
    assert data['telemetry']['call_count'] == 1
    assert data['usage'] == {'prompt': 7, 'completion': 3}


def test_explicit_transport_retries_are_all_recorded(monkeypatch):
    import httpx
    from openai import APIConnectionError
    from paper_agent import llm
    attempts = []
    class Completions:
        def create(self, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
                                   choices=[SimpleNamespace(message=SimpleNamespace(content='ok', tool_calls=[]))])
    monkeypatch.setattr(llm, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(llm, 'resolve_endpoint', lambda _: SimpleNamespace(base_url='https://example.invalid', model='fixture', api_key='secret'))
    monkeypatch.setattr('paper_agent.provider_calls.time.sleep', lambda _: None)
    ledger = CallLedger()
    with task_observation(ledger):
        assert llm.chat([{'role': 'user', 'content': 'hello'}], cfg={'llm': {}})[0] == 'ok'
    result = ledger.summary()
    assert result['call_count'] == 3 and result['failed_calls'] == 2
    assert result['unknown_usage_calls'] == 2 and result['prompt'] == 11
    assert 'secret' not in json.dumps(result)
