"""Bounded document windows with explicit coverage and omitted ranges."""

from __future__ import annotations

import json
import re

from paper_agent.services.versions import digest

MAX_SEGMENTS = 4
SEGMENT_CHARS = 36_000
_IMPORTANT = re.compile(r"^#{1,6}\s+.*(?:experiment|result|evaluation|appendi|supplement|实验|结果|附录)", re.I | re.M)


def document_windows(markdown: str, *, segment_chars=SEGMENT_CHARS, max_segments=MAX_SEGMENTS):
    if segment_chars < 1 or max_segments < 1:
        raise ValueError("分段预算必须为正整数")
    windows = []
    start = 0
    while start < len(markdown):
        end = min(len(markdown), start + segment_chars)
        if end < len(markdown):
            boundary = markdown.rfind("\n", start + segment_chars // 2, end)
            if boundary >= 0:
                end = boundary + 1
        windows.append({"start": start, "end": end, "text": markdown[start:end]})
        start = end
    selected = list(range(len(windows)))
    if len(windows) > max_segments:
        priority = [0, len(windows) - 1]
        priority += [i for i, w in enumerate(windows) if _IMPORTANT.search(w["text"])]
        priority += list(range(len(windows)))
        selected = sorted(list(dict.fromkeys(priority))[:max_segments])
    picked = [windows[i] for i in selected]
    upstream = None
    match = re.search(r"<!-- parse-coverage: (\{.*?\}) -->", markdown)
    if match:
        try:
            upstream = json.loads(match.group(1))
        except ValueError:
            pass
    covered = sum(w["end"] - w["start"] for w in picked)
    coverage = {
        "source_sha256": digest(markdown), "total_chars": len(markdown), "covered_chars": covered,
        "status": "complete" if covered == len(markdown) else "partial",
        "segment_budget": max_segments, "segment_char_budget": segment_chars,
        "ranges": [{"start": w["start"], "end": w["end"]} for w in picked],
        "omitted_ranges": [{"start": w["start"], "end": w["end"]} for i, w in enumerate(windows) if i not in selected],
        "upstream_parse": upstream,
    }
    return picked, coverage


def add_usage(total: dict, usage: dict) -> dict:
    return {"prompt": total.get("prompt", 0) + usage.get("prompt", 0),
            "completion": total.get("completion", 0) + usage.get("completion", 0),
            "model": usage.get("model") or total.get("model", "")}


def coverage_note(coverage: dict) -> str:
    upstream = coverage.get("upstream_parse") or {}
    limited = coverage["status"] == "partial" or upstream.get("truncated", False)
    state = "部分覆盖，未覆盖内容不能据此判定缺失" if limited else "已覆盖本次解析文本，仍需核对 PDF 原文与表格"
    return (f"输入覆盖：{coverage['covered_chars']}/{coverage['total_chars']} 字符，"
            f"{len(coverage['ranges'])} 段；{state}。")
