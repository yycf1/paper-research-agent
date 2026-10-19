"""Bounded SDK retries with one ledger entry per HTTP attempt."""
import time

from openai import APIConnectionError, APIStatusError

from paper_agent.observability import observed_call


def provider_call(fn, *, retry_rate_limit=True, **observation):
    for attempt in range(3):
        try:
            return observed_call(fn, **observation)
        except (APIConnectionError, APIStatusError) as exc:
            status = getattr(exc, 'status_code', None)
            body = getattr(exc, 'body', None)
            quota = any(word in str(body) for word in ('1113', 'insufficient_quota', '余额不足'))
            transient = isinstance(exc, APIConnectionError) or status in (408, 409) or (status is not None and status >= 500)
            transient = transient or (status == 429 and retry_rate_limit and not quota)
            if not transient or attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))
