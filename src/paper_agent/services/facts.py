"""Read existing facts and reviews, binding them to the current local index only."""

from __future__ import annotations

import json

from paper_agent import paths
from paper_agent.download import safe_dirname
from paper_agent.extraction.evidence import FactReview, build_bound_fields
from paper_agent.services.versions import EXPERIMENT_CONTRACT, artifact_current
from paper_agent.services.versions import _atomic_text
from paper_agent.services.locks import paper_lock


def load_bound_fields(paper_id, cfg, hits, *, max_chars=8000):
    directory = paths.knowledge_dir(cfg) / safe_dirname(paper_id)
    issues = []
    structured = {}
    try:
        data = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a fact object")
        structured = {k: data.get(k) for k in ("research_goal", "datasets", "metrics")}
    except FileNotFoundError:
        issues.append("extraction_not_available")
    except (ValueError, OSError):
        issues.append("extraction_unreadable")
    reviews = []
    md_path = paths.parsed_dir(cfg) / safe_dirname(paper_id) / "full_text.md"
    current = not md_path.exists() or artifact_current(directory / "experiment.json", md_path.read_text(encoding="utf-8"), cfg, EXPERIMENT_CONTRACT)
    if not current:
        issues.append("extraction_stale_or_unversioned")
    try:
        data = json.loads((directory / "fact_reviews.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
            raise ValueError("Expected reviews")
        reviews = [FactReview.model_validate(row) for row in data["reviews"]]
        if len({r.claim_sha256 for r in reviews}) != len(reviews):
            raise ValueError("Duplicate reviews")
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        issues.append("reviews_unreadable")
        reviews = []
    if not current:
        reviews = []
    result = build_bound_fields(
        paper_id, structured, hits, reviews=reviews, max_chars=max_chars
    )
    result["load_issues"] = issues
    return result


def save_fact_review(paper_id, cfg, lib, hits, review: FactReview, expected_version: str):
    with paper_lock(lib, paper_id):
        current = load_bound_fields(paper_id, cfg, hits, max_chars=100_000)
        if current["source_version"] != expected_version or current["load_issues"]:
            raise ValueError("原文、抽取或复核版本已变化或不可用；请刷新字段依据后重新核对")
        facts = [f for values in current["fields"].values() for f in values]
        fact = next((f for f in facts if f["claim_sha256"] == review.claim_sha256), None)
        if fact is None:
            raise ValueError("事实或依据版本已变化，请刷新后重新核对")
        if review.status == "supported" and (fact["normalized_value"] is None or fact["citation_status"] != "valid"):
            raise ValueError("缺少有效依据时不能标为支持；可标记不支持或冲突并填写备注")
        path = paths.knowledge_dir(cfg) / safe_dirname(paper_id) / "fact_reviews.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"reviews": []}
        data["reviews"] = [r for r in data["reviews"] if r["claim_sha256"] != review.claim_sha256]
        data["reviews"].append(review.model_dump(mode="json"))
        _atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        return load_bound_fields(paper_id, cfg, hits)
