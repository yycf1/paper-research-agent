"""Read existing facts and reviews, binding them to the current local index only."""

from __future__ import annotations

import json

from paper_agent import paths
from paper_agent.download import safe_dirname
from paper_agent.extraction.evidence import FactReview, build_bound_fields


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
    result = build_bound_fields(
        paper_id, structured, hits, reviews=reviews, max_chars=max_chars
    )
    result["load_issues"] = issues
    return result
