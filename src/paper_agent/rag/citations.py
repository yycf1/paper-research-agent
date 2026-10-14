"""Check numbered references, without claiming semantic support."""

from __future__ import annotations

import re

_BRACKETS = re.compile(r"\[\s*(-?\d+(?:\s*(?:[,，、]|[-–])\s*\d+)*)\s*\]")
_REFUSAL_PREFIXES = ("论文库中的内容未提及", "证据不足", "现有片段不足", "提供的片段不足")


def check_citations(answer: str, context_size: int) -> dict:
    numbers = []
    malformed_range = False
    for match in _BRACKETS.finditer(answer):
        for part in re.split(r"[,，、]", match.group(1)):
            interval = re.fullmatch(r"\s*(\d+)\s*[-–]\s*(\d+)\s*", part)
            if interval:
                lo, hi = map(int, interval.groups())
                if lo > hi or hi - lo > 100:
                    malformed_range = True
                else:
                    numbers.extend(range(lo, hi + 1))
            else:
                numbers.append(int(part.strip()))
    invalid = sorted({n for n in numbers if n < 1 or n > context_size})
    refusal = answer.strip().startswith(_REFUSAL_PREFIXES)
    missing = not numbers and not refusal
    return {
        "status": "failed" if invalid or malformed_range or missing else "passed",
        "numbers": numbers,
        "invalid_numbers": invalid,
        "missing_citation": missing,
        "malformed_range": malformed_range,
        "refusal_prefix": refusal,
        "semantic_support": "not_automatically_judged",
    }
