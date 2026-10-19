"""Outcome contracts checked against SQLite state and structured tool observations.

Evidence checks certify source presence and citation structure, never semantic truth.
Concurrent writers can invalidate a state delta; callers should isolate evaluation data.
"""
from __future__ import annotations

import hashlib
import json

from paper_agent.rag.citations import check_citations

KINDS = {'library_add', 'no_library_mutation', 'tool_success', 'evidence_answer'}


def validate_contract(checks):
    if checks is None:
        return []
    if not isinstance(checks, list) or len(checks) > 12:
        raise ValueError('验收条件须为不超过 12 项的列表')
    for check in checks:
        if (not isinstance(check, dict) or not isinstance(check.get('kind'), str)
                or check['kind'] not in KINDS):
            raise ValueError('未知验收条件')
        fields = {'library_add': {'kind', 'min_added', 'source_ids'},
                  'tool_success': {'kind', 'tools'},
                  'evidence_answer': {'kind'}, 'no_library_mutation': {'kind'}}
        if set(check) - fields[check['kind']]:
            raise ValueError('验收条件含未知字段')
        value = check.get('min_added', 1)
        if type(value) is not int or not 1 <= value <= 200:
            raise ValueError('min_added 必须为 1~200 的整数')
        for key in ('source_ids', 'tools'):
            if key in check and (not isinstance(check[key], list) or not check[key] or len(check[key]) > 200
                                 or any(not isinstance(v, str) or not v.strip() or len(v) > 150 for v in check[key])):
                raise ValueError(f'{key} 必须为非空字符串列表')
        if check['kind'] == 'tool_success' and not check.get('tools'):
            raise ValueError('tool_success 须指定 tools')
    return checks


def library_snapshot(executor) -> dict | None:
    lib = getattr(executor, '_lib', None)
    if lib is None:
        return None
    rows = [tuple(row) for row in lib.conn.execute('select * from papers order by source_id')]
    ids = [row[0] for row in lib.conn.execute('select source_id from papers order by source_id')]
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
    return {'ids': ids, 'sha256': digest}


def verify_outcome(run, *, before, after, checks=None) -> dict:
    checks = validate_contract(checks)
    records = []
    added = sorted(set(after['ids']) - set(before['ids'])) if before is not None and after is not None else None
    for check in checks:
        kind, passed, detail = check['kind'], None, ''
        if kind == 'library_add':
            if added is not None:
                ids = set(check.get('source_ids', []))
                reported = {sid for s in run.steps if s.tool == 'add_papers' and s.status == 'ok'
                            for sid in (s.observation or {}).get('selected_ids', [])}
                actual = set(added) & reported
                passed = len(actual) >= check.get('min_added', 1) and ids.issubset(actual)
                detail = f'账本新增且入库步骤确认的题录 {len(actual)} 篇；目标至少 {check.get("min_added", 1)} 篇'
            else:
                detail = '账本快照不可用'
        elif kind == 'no_library_mutation':
            passed = before['sha256'] == after['sha256'] if before is not None and after is not None else None
            detail = '比较执行前后题录、状态与全文路径' if passed is not None else '账本快照不可用'
        elif kind == 'tool_success':
            success = {s.tool for s in run.steps if s.status == 'ok'}
            missing = sorted(set(check['tools']) - success)
            passed, detail = not missing, ('未成功执行：' + '、'.join(missing) if missing else '指定工具均返回正常结果')
        elif kind == 'evidence_answer':
            answers = [s.observation or {} for s in run.steps if s.tool == 'ask_library' and s.status == 'ok']
            passed = False
            for answer in answers:
                sources = answer.get('sources') or []
                chunks = [chunk for source in sources for chunk in source.get('chunks', [])]
                numbers = {chunk.get('n') for chunk in chunks}
                citation = check_citations(answer.get('answer', ''), len(chunks))
                if (answer.get('answer_status') == 'generated' and chunks
                        and numbers == set(range(1, len(chunks) + 1))
                        and all(chunk.get('text', '').strip() for chunk in chunks)
                        and citation['status'] == 'passed' and citation['numbers']
                        and after is not None and all(source.get('paper_id') in after['ids'] for source in sources)):
                    passed = True
                    break
            detail = '核对检索来源与引用结构；结论语义仍需阅读原文核对'
            if not passed:
                reasons = sorted({a.get('answer_status') or '未取得有效回答' for a in answers})
                labels = {'scope_unindexed': '所选论文尚未建立索引', 'empty_index': '资料索引为空',
                          'no_hits': '本次没有召回证据', 'low_evidence': '证据相关性不足',
                          'citation_invalid': '回答引用结构无效', 'refused': '现有证据不足',
                          'generated': '来源或引用复查未通过'}
                detail = '；'.join(labels.get(reason, reason) for reason in reasons) or '问答步骤未成功执行'
        records.append({'kind': kind, 'passed': passed, 'detail': detail})
    finished = run.stopped_reason == 'final' and bool(run.answer.strip())
    failures = [s for s in run.steps if s.status != 'ok']
    if not finished or any(r['passed'] is False for r in records):
        status = 'failed'
    elif failures:
        status = 'partial'
    elif not records or any(r['passed'] is None for r in records):
        status = 'unverified'
    else:
        status = 'passed'
    return {'status': status, 'execution_finished': finished, 'checks': records,
            'added_ids': added, 'failed_steps': len(failures),
            'note': ('按所选条件核验执行结果；来源与引用结构检查不证明全部回答的语义正确。' if checks else
                     '未设置目标验收条件，仅记录执行过程；模型回复不作为成功凭据。'),
            'before_sha256': before['sha256'] if before else None,
            'after_sha256': after['sha256'] if after else None}
