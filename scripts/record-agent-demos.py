"""Record real-model runs on original fixtures; opt in with --live (API usage).

Run from the project root with this checkout's src on PYTHONPATH. Credentials are
read from the chosen configuration root; no personal library is opened or changed.
Every invocation keeps its own traces, including failed attempts. Replays are read-only.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import pymupdf

from paper_agent import config, llm, paths
from paper_agent.agent import ToolExecutor, run_agent_task
from paper_agent.download import safe_dirname
from paper_agent.models import Paper
from paper_agent.observability import CallLedger, task_observation, call_stage
from paper_agent.parsing.pdf import extract_markdown
from paper_agent.rag.embedder import embed
from paper_agent.rag.qa import Retriever
from paper_agent.rag.splitter import split_markdown
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.services.versions import index_metadata, save_artifact, file_digest, parser_contract

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='允许调用当前配置的真实模型服务')
    parser.add_argument('--config-root', type=Path, default=ROOT)
    args = parser.parse_args()
    if not args.live:
        parser.error('录制会消耗 API 用量；确认需要真实录制时添加 --live')
    config.PROJECT_ROOT = args.config_root.resolve()
    config.CONFIG_PATH = config.PROJECT_ROOT / 'config.yaml'
    config.ENV_PATH = config.PROJECT_ROOT / '.env'
    cfg = deepcopy(config.load_config())
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    output = ROOT / 'data' / 'agent-demos' / stamp
    output.mkdir(parents=True, exist_ok=False)
    cfg['data_dir'] = str(output)
    cfg['retrieval'] = {**cfg.get('retrieval', {}), 'mode': 'hybrid'}
    cfg['agent'] = {**cfg.get('agent', {}), 'max_steps': 5, 'token_budget': 18000}
    sid, missing = 'local:demo-evidence', 'local:demo-unindexed'
    title = 'Demo-M: Original Synthetic Retrieval Evidence'
    fixture = (ROOT / 'eval' / 'fixtures' / 'agent-evidence.txt').read_text(encoding='utf-8')
    original = paths.papers_dir(cfg) / safe_dirname(sid) / 'paper.pdf'
    original.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        doc.set_metadata({'title': title})
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), fixture, fontsize=10)
        doc.save(original)
    markdown = extract_markdown(original)
    assert 'Recall@5 is 0.72' in markdown, 'Fixture PDF text was clipped'
    parsed = paths.parsed_dir(cfg) / safe_dirname(sid) / 'full_text.md'
    save_artifact(parsed, markdown, file_digest(original), cfg, parser_contract())
    chunks = split_markdown(markdown, sid, title)
    store = VectorStore(paths.vector_dir(cfg))
    seed_ledger = CallLedger()
    with task_observation(seed_ledger, cfg), call_stage('demo.index_fixture'):
        vectors = embed([c.text for c in chunks], cfg=cfg)
    store.upsert_paper_chunks(sid, [c.text for c in chunks], index_metadata(markdown, chunks, cfg, 'fulltext'), vectors)
    (output / 'setup-usage.json').write_text(json.dumps(seed_ledger.summary(), ensure_ascii=False, indent=2), encoding='utf-8')
    lib = paths.library(cfg)
    try:
        lib.upsert_paper(Paper(source='local', source_id=sid, title=title))
        lib.set_status(sid, 'indexed', pdf_path=str(original))
        lib.upsert_paper(Paper(source='local', source_id=missing, title='Original Unindexed Fixture'))
        cases = [
            {'id': 'success', 'ids': [sid], 'expected': 'passed',
             'task': '先查看所选论文的数量与状态，再查询 Demo-M 的 Recall@5 数值、数据划分和评估协议，用库内原文证据回答。仅查看和问答，不新增题录，不运行代码。'},
            {'id': 'failure', 'ids': [missing], 'expected': 'failed',
             'task': '先查看所选论文的数量与状态，再尝试用库内问答检索这篇论文的 Recall@5 数值与评估协议，给出原文出处。如果没有索引或证据，请明确说明失败原因，不能猜测。仅查看和问答。'},
        ]
        demos = []
        for case in cases:
            print('recording', case['id'], flush=True)
            trace = output / (case['id'] + '.jsonl')
            executor = ToolExecutor(lib=lib, retriever=Retriever(store, cfg=cfg), cfg=cfg,
                                    allowed_paper_ids=case['ids'])
            checks = [{'kind': 'tool_success', 'tools': ['library_status', 'ask_library']},
                      {'kind': 'evidence_answer'}, {'kind': 'no_library_mutation'}]
            result = run_agent_task(case['task'], executor=executor, cfg=cfg, acceptance=checks,
                                    trace_path=trace,
                                    chat_fn=lambda messages, tools: llm.chat_with_tools(messages, tools=tools, cfg=cfg, max_tokens=1536),
                                    on_event=lambda step: print('step', step.tool, step.status, flush=True))
            record = {'id': f'{case["id"]}-{stamp}', 'task': case['task'],
                      'status': 'error' if result.stopped_reason == 'error' else 'done',
                      'steps': [asdict(s) for s in result.steps], 'step_count': len(result.steps),
                      'answer': result.answer, 'stopped_reason': result.stopped_reason,
                      'usage': result.usage, 'telemetry': result.telemetry,
                      'verification': result.verification, 'duration_ms': result.duration_ms,
                      'error': result.answer if result.stopped_reason == 'error' else '',
                      'pending_confirm': None, 'allowed_paper_ids': case['ids'],
                      'provenance': '真实模型决策与问答、真实向量/重排序服务；原创隔离 PDF；失败案例为未索引资料。',
                      'recorded_at': datetime.now(timezone.utc).isoformat(),
                      'expected_outcome': case['expected'], 'acceptance': checks,
                      'trace_sha256': hashlib.sha256(trace.read_bytes()).hexdigest(),
                      'trace_file': f'demo-traces/{stamp}-{case["id"]}.jsonl',
                      'fixture_sha256': hashlib.sha256(fixture.encode()).hexdigest()}
            demos.append(record)
            (output / (case['id'] + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'case': case['id'], 'outcome': result.verification['status'],
                              'calls': result.telemetry['call_count'], 'usage': result.usage}, ensure_ascii=False), flush=True)
        manifest = {'schema_version': 1, 'fixture': 'original synthetic evidence, not a research benchmark',
                    'setup_telemetry': seed_ledger.summary(), 'demos': demos}
        (output / 'agent_demos.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        # Keep all attempts in data. Publish the replay only when both intended branches were exercised.
        valid = all(d['verification']['status'] == d['expected_outcome'] and d['step_count'] >= 2 for d in demos)
        if not valid:
            raise RuntimeError(f'演示分支未全部通过检查，原始记录已保留在 {output}')
        for case, demo in zip(cases, demos):
            target = ROOT / 'eval' / demo['trace_file']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((output / (case['id'] + '.jsonl')).read_bytes())
        (ROOT / 'eval' / 'agent_demos.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print('saved', str(output), flush=True)
    finally:
        lib.close()


if __name__ == '__main__':
    main()
