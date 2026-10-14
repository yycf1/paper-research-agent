from copy import deepcopy

import pytest

from paper_agent.services.comparison import compare_metric_conditions, comparison_report


def fact(**changes):
    value = {"name": "Recall@10", "task": "retrieval", "dataset": "Synthetic-A", "split": "fixed test set",
             "definition": "recall at 10", "unit": "ratio", "evaluation_protocol": "full candidate set", "value": "0.5"}
    value.update(changes)
    return {"normalized_value": value, "citation_status": "valid", "support_status": "unverified"}


@pytest.mark.parametrize("key,changed", [("name", "Recall@20"), ("split", "random split"), ("unit", "percent"),
                                         ("dataset", "Synthetic-B"), ("evaluation_protocol", "sampled negatives")])
def test_explicit_condition_differences_prevent_ranking(key, changed):
    result = compare_metric_conditions(fact(), fact(**{key: changed}))
    assert result["status"] == "different" and result["different_conditions"]
    assert result["ranking_allowed"] is False


def test_missing_conditions_remain_unknown():
    result = compare_metric_conditions(fact(), fact(definition="", split=""))
    assert result["status"] == "incomplete" and len(result["missing_conditions"]) == 2


def test_matching_strings_and_valid_citations_are_not_semantic_review():
    result = compare_metric_conditions(fact(), fact(value="0.8"))
    assert result["status"] == "unverified" and result["evidence_reviewed"] is False
    assert result["ranking_allowed"] is False


def test_reviewed_matching_conditions_still_do_not_prove_fair_ranking():
    left, right = fact(), fact()
    for row in (left, right):
        row["support_status"] = "supported"
    result = compare_metric_conditions(left, right)
    assert result["status"] == "matching_reviewed" and result["ranking_allowed"] is False


def test_report_is_bounded_and_keeps_paper_ids():
    paper = {"paper_id": "p1", "field_evidence": {"fields": {"metrics": [fact() for _ in range(8)]}}}
    other = deepcopy(paper)
    other["paper_id"] = "p2"
    pair = comparison_report([paper, other])["pairs"][0]
    assert pair["paper_ids"] == ["p1", "p2"]
    assert len(pair["metrics"]) == 4 and pair["omitted_pairs"] == 60
    assert comparison_report([{"paper_id": "p1"}, {"paper_id": "p2"}])["pairs"][0]["status"] == "metrics_missing"
