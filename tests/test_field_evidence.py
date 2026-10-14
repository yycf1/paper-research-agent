import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from paper_agent import paths
from paper_agent.agent.executor import ToolExecutor
from paper_agent.agent.loop import run_agent_task
from paper_agent.download import safe_dirname
from paper_agent.extraction.evidence import (
    FactReview,
    bind_fact,
    build_bound_fields,
    source_version,
    validate_reference,
)
from paper_agent.library import Library
from paper_agent.llm import ChatResponse, ToolCall
from paper_agent.models import Paper
from paper_agent.rag.vectorstore import Hit
from paper_agent.server import routes
from paper_agent.server.app import create_app
from paper_agent.services.facts import load_bound_fields
from paper_agent.tools import ToolResult


def hit(
    text="Dataset ORIGA; Dice 0.91, Accuracy 0.99.", paper="p1", chunk="p1::0", page=2
):
    return Hit(paper, "Paper", "Results", page, text, 0.1, chunk_id=chunk)


def review(fact, status="supported"):
    return {
        "claim_sha256": fact.claim_sha256,
        "reviewer": "test reviewer",
        "review_kind": "human",
        "status": status,
        "note": "This is a test-only declared review.",
    }


def test_lexical_candidates_are_bound_but_not_semantically_certified():
    h = hit()
    fact = bind_fact("datasets", {"name": "ORIGA", "split": ""}, "p1", [h])
    assert fact.citation_status == "valid"
    assert fact.support_status == "unverified"
    ref = fact.evidence[0]
    assert h.text[ref.start : ref.end] == ref.quote
    assert ref.page == 2 and ref.paper_id == "p1"
    # The number belongs to a different metric. A lexical match must not prove the claim.
    wrong = bind_fact("metrics", {"name": "Dice", "value": "0.99"}, "p1", [h])
    assert wrong.support_status == "unverified"


@pytest.mark.parametrize(
    "field,raw", [("research_goal", None), ("datasets", []), ("metrics", {"name": 4})]
)
def test_missing_or_invalid_values_remain_unknown(field, raw):
    fact = bind_fact(field, raw, "p1", [hit()])
    assert fact.support_status == "unknown"
    assert fact.normalized_value is None


@pytest.mark.parametrize(
    "change,issue",
    [
        ({"paper_id": "p2"}, "wrong_paper"),
        ({"chunk_id": "missing"}, "unknown_or_ambiguous_chunk"),
        ({"page": 3}, "page_mismatch"),
        ({"section": "Other"}, "section_mismatch"),
        ({"quote": "Invented evidence"}, "quote_mismatch"),
        ({"text_sha256": "old"}, "stale_reference"),
        ({"source_version": "old"}, "stale_reference"),
    ],
)
def test_wrong_or_stale_references_cannot_be_attached(change, issue):
    h = hit()
    fact = bind_fact("datasets", {"name": "ORIGA"}, "p1", [h])
    bad = {**fact.evidence[0].model_dump(), **change}
    bound = bind_fact("datasets", {"name": "ORIGA"}, "p1", [h], proposals=[bad])
    assert bound.citation_status == "invalid"
    assert bound.evidence == []
    assert issue in bound.issues


def test_nonexistent_metric_value_does_not_get_a_candidate():
    fact = bind_fact("metrics", {"name": "Dice", "value": "0.77"}, "p1", [hit()])
    assert fact.citation_status == "missing"
    assert fact.support_status == "unverified"


def test_numeric_tokens_do_not_match_part_of_a_different_number():
    fact = bind_fact(
        "metrics", {"name": "Dice", "value": "0.9"}, "p1", [hit("Dice 0.91 on ORIGA.")]
    )
    assert fact.evidence == []


def test_dataset_name_may_be_followed_by_sentence_punctuation():
    fact = bind_fact(
        "datasets", {"name": "ORIGA"}, "p1", [hit("We evaluate on ORIGA.")]
    )
    assert fact.citation_status == "valid"


def test_metric_number_may_be_followed_by_sentence_punctuation():
    fact = bind_fact(
        "metrics", {"name": "Dice", "value": "0.91"}, "p1", [hit("Dice 0.91.")]
    )
    assert fact.citation_status == "valid"


def test_index_read_order_does_not_change_claim_or_review_binding():
    corpus = [hit("Dataset ORIGA.", chunk=f"p1::{i}", page=i + 1) for i in range(4)]
    raw = {"name": "ORIGA"}
    first = bind_fact("datasets", raw, "p1", corpus)
    reordered = bind_fact("datasets", raw, "p1", list(reversed(corpus)), review=review(first))
    assert first.claim_sha256 == reordered.claim_sha256
    assert reordered.support_status == "supported"


def test_declared_review_is_bound_to_value_source_and_evidence():
    h = hit()
    raw = {"name": "ORIGA"}
    fact = bind_fact("datasets", raw, "p1", [h])
    confirmed = bind_fact("datasets", raw, "p1", [h], review=review(fact))
    assert confirmed.support_status == "supported"
    changed = bind_fact("datasets", {"name": "OTHER"}, "p1", [h], review=review(fact))
    assert changed.support_status == "unverified" and "stale_review" in changed.issues
    changed_source = replace(h, text=h.text + " New version.")
    changed = bind_fact("datasets", raw, "p1", [changed_source], review=review(fact))
    assert changed.support_status == "unverified" and "stale_review" in changed.issues
    old_ref = fact.evidence[0]
    assert "stale_reference" in validate_reference(
        old_ref, "p1", [changed_source], source_version("p1", [changed_source])
    )


def test_support_requires_evidence_and_assistant_is_not_a_human_review():
    fact = bind_fact("datasets", {"name": "OTHER"}, "p1", [hit()])
    confirmed = bind_fact(
        "datasets", fact.raw_value, "p1", [hit()], review=review(fact)
    )
    assert confirmed.support_status == "unverified"
    assert "support_requires_valid_evidence" in confirmed.issues
    with pytest.raises(ValueError):
        FactReview.model_validate({**review(fact), "review_kind": "assistant"})


@pytest.mark.parametrize("status", ["conflict", "unsupported", "not_applicable"])
def test_explicit_review_outcomes_are_preserved(status):
    h = hit()
    fact = bind_fact("datasets", {"name": "ORIGA"}, "p1", [h])
    checked = bind_fact(
        "datasets", fact.raw_value, "p1", [h], review=review(fact, status)
    )
    assert checked.support_status == status


def test_binder_filters_cross_paper_candidates_and_preserves_unknown_fields():
    bound = build_bound_fields(
        "p1", {"datasets": [{"name": "ORIGA"}]}, [hit(paper="p2", chunk="p2::0")]
    )
    assert bound["fields"]["datasets"][0]["evidence"] == []
    assert bound["fields"]["research_goal"][0]["support_status"] == "unknown"
    assert not bound["numeric_comparison"]["ranking_allowed"]


def test_budget_preserves_all_field_slots_and_reports_omissions():
    raw = {
        "research_goal": "Aims to evaluate models.",
        "datasets": [{"name": f"DATA{i}", "split": "x" * 100} for i in range(12)],
        "metrics": [{"name": "Dice", "value": "0.91"} for _ in range(12)],
    }
    bound = build_bound_fields(
        "p1",
        raw,
        [hit("Aims to evaluate models. DATA0 DATA1 DATA2 DATA3. Dice 0.91.")],
        max_chars=6000,
    )
    assert len(json.dumps(bound, ensure_ascii=False)) <= 6000
    assert all(
        bound["fields"][field] for field in ("research_goal", "datasets", "metrics")
    )
    assert sum(bound["omitted"].values()) > 0


def test_loader_rejects_duplicate_or_unreadable_reviews(tmp_path):
    cfg = {"data_dir": str(tmp_path)}
    directory = paths.knowledge_dir(cfg) / safe_dirname("p1")
    directory.mkdir(parents=True)
    (directory / "experiment.json").write_text(
        json.dumps({"datasets": [{"name": "ORIGA"}]}), encoding="utf-8"
    )
    fact = bind_fact("datasets", {"name": "ORIGA"}, "p1", [hit()])
    (directory / "fact_reviews.json").write_text(
        json.dumps({"reviews": [review(fact), review(fact)]}), encoding="utf-8"
    )
    result = load_bound_fields("p1", cfg, [hit()])
    assert "reviews_unreadable" in result["load_issues"]
    assert result["fields"]["datasets"][0]["support_status"] == "unverified"


def test_facts_api_reads_only_current_paper_without_model_calls(tmp_path, monkeypatch):
    cfg = {"data_dir": str(tmp_path)}
    lib = paths.library(cfg)
    lib.upsert_paper(
        Paper(source="openalex", source_id="openalex:W1", title="Local paper")
    )
    lib.close()
    corpus = [hit(paper="openalex:W1", chunk="openalex:W1::0")]

    class LocalStore:
        def __init__(self, *args):
            pass

        def all_hits(self, *, paper_id=None):
            assert paper_id == "openalex:W1"
            return corpus

    monkeypatch.setattr(routes, "load_config", lambda: cfg)
    monkeypatch.setattr(routes, "VectorStore", LocalStore)
    client = TestClient(create_app())
    response = client.get("/api/papers/openalex:W1/facts")
    assert response.status_code == 200
    assert response.json()["fields"]["datasets"][0]["support_status"] == "unknown"
    assert client.get("/api/papers/openalex:missing/facts").status_code == 404


def test_compare_payload_remains_bounded_and_all_papers_have_field_records(tmp_path):
    cfg = {"data_dir": str(tmp_path)}
    lib = Library(tmp_path / "library.db")

    class LocalStore:
        def count(self):
            return 6

        def all_hits(self, *, paper_id=None):
            return [
                hit(
                    "Dataset ORIGA. Dice 0.91. " + "\n" * 6000,
                    paper=paper_id,
                    chunk=f"{paper_id}::0",
                )
            ]

    class LocalRetriever:
        store = LocalStore()

        def retrieve(self, query, *, k=3, paper_id=None):
            return self.store.all_hits(paper_id=paper_id)

    try:
        ids = [f"openalex:W{i}" for i in range(6)]
        for sid in ids:
            lib.upsert_paper(Paper(source="openalex", source_id=sid, title=sid))
            lib.set_status(sid, "indexed")
            directory = paths.knowledge_dir(cfg) / safe_dirname(sid)
            directory.mkdir(parents=True)
            (directory / "experiment.json").write_text(
                json.dumps(
                    {
                        "research_goal": "x" * 2000,
                        "datasets": [
                            {"name": "ORIGA", "source": "x" * 1000} for _ in range(8)
                        ],
                        "metrics": [
                            {"name": "Dice", "value": "0.91"} for _ in range(8)
                        ],
                    }
                ),
                encoding="utf-8",
            )
        result = ToolExecutor(lib=lib, cfg=cfg, retriever=LocalRetriever()).execute(
            "compare_papers",
            json.dumps(
                {"topic": "datasets and metrics", "paper_ids": ids, "k_per_paper": 5}
            ),
        )
        assert result.ok, result.error
        assert (
            len(
                json.dumps(
                    {
                        "status": result.status,
                        "data": result.data,
                        "error": result.error,
                    },
                    ensure_ascii=False,
                )
            )
            < 100_000
        )
        assert [p["paper_id"] for p in result.data["papers"]] == ids
        assert all(
            p["field_evidence"]["fields"]["metrics"] for p in result.data["papers"]
        )
    finally:
        lib.close()


def test_comparison_evidence_survives_model_context_events_and_trace(tmp_path):
    fields = build_bound_fields("p1", {"datasets": [{"name": "ORIGA"}]}, [hit()])
    expected = {
        "comparability": {"ranking_allowed": False, "pairs": []},
        "papers": [{"paper_id": "p1", "title": "Paper", "field_evidence": fields}]
    }

    class Executor:
        def specs_for(self):
            return ToolExecutor.specs()

        def execute(self, name, arguments):
            return ToolResult(tool=name, status="ok", data=expected)

    captured = []
    responses = [
        ChatResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1", name="compare_papers", arguments='{"topic":"datasets"}'
                )
            ],
            usage={},
        ),
        ChatResponse(content="完成", tool_calls=[], usage={}),
    ]

    def chat(messages, *, tools):
        captured.append(list(messages))
        return responses.pop(0)

    events = []
    trace = tmp_path / "trace.jsonl"
    result = run_agent_task(
        "比较数据集",
        executor=Executor(),
        chat_fn=chat,
        on_event=events.append,
        trace_path=trace,
    )
    assert result.steps[0].comparison == expected
    assert events[0].comparison == expected
    tool_message = next(m for m in captured[1] if m["role"] == "tool")
    assert json.loads(tool_message["content"])["data"] == expected
    saved = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    assert next(row for row in saved if row["type"] == "step")["comparison"] == expected


def test_changed_evidence_budget_does_not_retain_semantic_review():
    raw = {"name": "ORIGA", "split": "x" * 900}
    corpus = [hit("ORIGA. " + "description " * 30)]
    fact = bind_fact("datasets", raw, "p1", corpus)
    full = build_bound_fields("p1", {"datasets": [raw]}, corpus, reviews=[review(fact)])
    assert full["fields"]["datasets"][0]["support_status"] == "supported"
    limited = build_bound_fields(
        "p1", {"datasets": [raw]}, corpus, reviews=[review(fact)], max_chars=2700
    )
    assert limited["fields"]["datasets"][0]["support_status"] != "supported"
    assert limited["unmatched_reviews"] == 1
