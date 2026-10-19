"""Task-local provider calls. No prompts, credentials, or guessed prices are logged."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time

_collector: ContextVar[CallLedger | None] = ContextVar('call_ledger', default=None)
_stage: ContextVar[str] = ContextVar('call_stage', default='model')
_config: ContextVar[dict | None] = ContextVar('task_config', default=None)


def normalize_usage(usage) -> dict:
    if not isinstance(usage, dict):
        usage = {key: getattr(usage, key, None) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    out = {}
    for name, keys in {'prompt': ('prompt', 'prompt_tokens', 'input_tokens'),
                       'completion': ('completion', 'completion_tokens', 'output_tokens'),
                       'total': ('total', 'total_tokens')}.items():
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                out[name] = value
                break
    if 'prompt' in out and 'completion' in out:
        out.setdefault('total', out['prompt'] + out['completion'])
    return out


def task_config() -> dict | None:
    return _config.get()


def current_ledger() -> CallLedger | None:
    return _collector.get()


class CallLedger:
    def __init__(self, on_call=None):
        self.calls: list[dict] = []
        self.on_call = on_call

    def summary(self) -> dict:
        chat = [c for c in self.calls if c['kind'] == 'chat']
        known = [c for c in self.calls if c['usage_known']]
        return {'call_count': len(self.calls), 'known_usage_calls': len(known),
                'unknown_usage_calls': len(self.calls) - len(known),
                'usage_complete': len(known) == len(self.calls),
                'prompt': sum(c['usage'].get('prompt', 0) for c in chat),
                'completion': sum(c['usage'].get('completion', 0) for c in chat),
                'embedding_tokens': sum(c['usage'].get('total', c['usage'].get('prompt', 0)) for c in self.calls if c['kind'] == 'embedding'),
                'rerank_tokens': sum(c['usage'].get('total', 0) for c in self.calls if c['kind'] == 'rerank'),
                'elapsed_ms': sum(c['elapsed_ms'] for c in self.calls),
                'failed_calls': sum(c['status'] == 'error' for c in self.calls),
                'cost': None, 'cost_note': '未配置价格，未估算费用',
                'calls': [dict(c) for c in self.calls]}


@contextmanager
def task_observation(ledger: CallLedger, cfg=None):
    token, config_token = _collector.set(ledger), _config.set(cfg)
    try:
        yield ledger
    finally:
        _config.reset(config_token)
        _collector.reset(token)


@contextmanager
def call_stage(stage: str):
    token = _stage.set(stage)
    try:
        yield
    finally:
        _stage.reset(token)


def current_summary() -> dict:
    ledger = _collector.get()
    return ledger.summary() if ledger else {}


def observed_call(fn, *, kind='chat', model='', usage_of=lambda r: r.usage,
                  fallback=False, units=None):
    """fallback wraps injected functions; real adapters own their records (no double count)."""
    ledger = _collector.get()
    if ledger is None:
        return fn()
    before = len(ledger.calls)
    record = {'id': before + 1, 'kind': kind, 'stage': _stage.get(), 'model': model,
              'status': 'running', 'usage': {}, 'usage_known': False, 'units': units or {}}
    if not fallback:
        ledger.calls.append(record)
    started = time.perf_counter()
    try:
        result = fn()
        usage = usage_of(result)
        if not model and isinstance(usage, dict):
            record['model'] = str(usage.get('model', ''))
        record['usage'] = normalize_usage(usage)
        record['usage_known'] = (all(k in record['usage'] for k in ('prompt', 'completion'))
                                 if kind == 'chat' else bool(record['usage']))
        record['status'] = 'ok'
        return result
    except Exception as exc:
        record.update(status='error', error_type=type(exc).__name__)
        raise
    finally:
        record['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
        owned = not fallback or len(ledger.calls) == before
        if fallback and owned:
            ledger.calls.append(record)
        if owned and ledger.on_call:
            ledger.on_call(dict(record))
