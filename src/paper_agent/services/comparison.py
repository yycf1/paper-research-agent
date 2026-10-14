"""Explain explicit experiment-condition gaps without ranking raw metrics."""

from __future__ import annotations

from itertools import combinations

CONDITIONS = {"name": "指标名称/参数", "task": "任务", "dataset": "数据集", "split": "数据划分",
              "definition": "指标定义", "unit": "单位", "evaluation_protocol": "评估协议"}


def _text(value):
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def compare_metric_conditions(left: dict, right: dict) -> dict:
    a = left.get("normalized_value") or {}
    b = right.get("normalized_value") or {}
    if not isinstance(a, dict) or not isinstance(b, dict):
        a, b = {}, {}
    missing = [label for key, label in CONDITIONS.items() if not _text(a.get(key)) or not _text(b.get(key))]
    different = [label for key, label in CONDITIONS.items()
                 if _text(a.get(key)) and _text(b.get(key)) and _text(a[key]) != _text(b[key])]
    verified = all(f.get("citation_status") == "valid" and f.get("support_status") == "supported" for f in (left, right))
    status = "different" if different else "incomplete" if missing else "matching_reviewed" if verified else "unverified"
    return {"left_metric": a.get("name"), "right_metric": b.get("name"), "status": status,
            "missing_conditions": missing, "different_conditions": different,
            "evidence_reviewed": verified, "ranking_allowed": False,
            "reason": "仅核对已记录条件；相同字符串和有效引用都不自动证明实验公平可比，不据裸值排名。"}


def comparison_report(papers: list[dict]) -> dict:
    pairs = []
    for left, right in combinations(papers, 2):
        a = left.get("field_evidence", {}).get("fields", {}).get("metrics", [])
        b = right.get("field_evidence", {}).get("fields", {}).get("metrics", [])
        candidates = [(x, y) for x in a for y in b
                      if isinstance(x.get("normalized_value"), dict) and isinstance(y.get("normalized_value"), dict)
                      and _text(x["normalized_value"].get("name")) == _text(y["normalized_value"].get("name"))]
        if not candidates and a and b:
            candidates = [(a[0], b[0])]
        pairs.append({"paper_ids": [left["paper_id"], right["paper_id"]],
                      "metrics": [compare_metric_conditions(x, y) for x, y in candidates[:4]],
                      "omitted_pairs": max(0, len(candidates) - 4),
                      "status": "assessed_recorded_fields" if candidates else "metrics_missing"})
    return {"pairs": pairs, "ranking_allowed": False,
            "note": "同名指标也需检查任务、划分、定义与单位。缺失条件必须保留未知；条件核对未包含未记录的混杂因素。"}
