"""Optional diagnostic records; never record endpoint credentials or embeddings."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hit_record(hit) -> dict:
    row = asdict(hit)
    row["text_sha256"] = text_hash(hit.text)
    return row


def capture_stage(trace: dict | None, stage: str, hits, *, status="ok", started=None) -> None:
    if trace is None:
        return
    trace["stages"][stage] = [hit_record(h) for h in hits]
    trace["stage_status"][stage] = status
    if started is not None:
        trace["timings_ms"][stage] = round((time.perf_counter() - started) * 1000, 3)
