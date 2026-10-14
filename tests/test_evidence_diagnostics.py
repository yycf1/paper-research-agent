import json
from copy import deepcopy
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from paper_agent.cli.core import app
from paper_agent.rag.diagnostics import (
    apply_reviews,
    corpus_fingerprint,
    diagnose_case,
    load_cases,
    load_reviews,
    load_snapshot,
    run_diagnostics,
    write_report,
    write_snapshot,
)
from paper_agent.rag.qa import Retriever, answer_question
from paper_agent.rag.tracing import hit_record, text_hash
from paper_agent.rag.vectorstore import Hit


def hit(cid, text, paper="p1"):
    return Hit(paper, "Paper", "Methods", 2, text, 0.1, chunk_id=cid)


def reference(h):
    return {"paper_id": h.paper_id, "chunk_id": h.chunk_id, "text_sha256": text_hash(h.text), "quote": h.text}


def case(h):
    return {
        "id": "q1",
        "question": "needle",
        "annotation_status": "text_checked",
        "should_answer": True,
        "evidence_groups": [[reference(h)]],
        "expected_facts": ["a fact"],
    }


def dataset(corpus, cases):
    return {"schema_version": 1, "corpus_sha256": corpus_fingerprint(corpus), "cases": cases}


class Store:
    def __init__(self, corpus, dense=None):
        self.corpus, self.dense = corpus, dense if dense is not None else corpus

    def all_hits(self, *, paper_id=None):
        return [h for h in self.corpus if paper_id is None or h.paper_id == paper_id]

    def query(self, vector, *, k, paper_id=None):
        return [h for h in self.dense if paper_id is None or h.paper_id == paper_id][:k]


def diagnose(c, trace, corpus, review=None, version=None):
    return diagnose_case(
        c,
        trace,
        corpus,
        corpus_sha=corpus_fingerprint(corpus),
        dataset_corpus_sha=version or corpus_fingerprint(corpus),
        review=review,
    )


def test_bm25_diagnostics_are_offline_and_do_not_claim_answer_accuracy():
    gold = hit("a", "needle fact")
    corpus = [gold, hit("b", "other")]
    retriever = Retriever(
        Store(corpus),
        cfg={"retrieval": {"mode": "bm25"}},
        embed_fn=lambda _: pytest.fail("Offline mode must not embed"),
    )
    report = run_diagnostics(dataset(corpus, [case(gold)]), corpus, retriever, k=1)
    assert report["cases_scored"] == 1
    assert report["retrieval_metrics"]["hit_at_k"] == 1
    row = report["cases"][0]
    assert row["trace"]["retrieval"]["stage_status"]["dense"] == "not_run"
    assert row["trace"]["answer"] is None
    assert row["review"]["answer_correct"] is None
    assert row["failure_stage"] == "answer_review_pending"


def test_reranking_loss_is_distinct_from_candidate_recall_loss():
    gold, other = hit("a", "needle fact"), hit("b", "unrelated")
    corpus = [gold, other]
    retriever = Retriever(
        Store(corpus),
        embed_fn=lambda _: [[1.0]],
        cfg={"retrieval": {"candidate_k": 2}, "reranker": {"enabled": True}},
        rerank_fn=lambda q, hs, cfg, k: [other],
    )
    report = run_diagnostics(dataset(corpus, [case(gold)]), corpus, retriever, k=1)
    row = report["cases"][0]
    assert row["failure_stage"] == "rerank_or_topk_cut"
    assert row["trace"]["retrieval"]["stages"]["dense"][0]["chunk_id"] == "a"
    assert row["trace"]["retrieval"]["stages"]["final"][0]["chunk_id"] == "b"
    missing_case = {**case(gold), "question": "absent"}
    vector = Retriever(Store(corpus, [other]), cfg={"retrieval": {"mode": "vector"}}, embed_fn=lambda _: [[1.0]])
    missing = run_diagnostics(dataset(corpus, [missing_case]), corpus, vector, k=1)
    assert missing["cases"][0]["failure_stage"] == "candidate_recall_miss"


def test_trace_preserves_rerank_fallback_and_existing_results():
    corpus = [hit("a", "needle"), hit("b", "other")]
    retriever = Retriever(
        Store(corpus),
        embed_fn=lambda _: [[1.0]],
        cfg={"reranker": {"enabled": True}},
        rerank_fn=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private provider text")),
    )
    before = retriever.retrieve("needle", k=1)
    trace = {}
    after = retriever.retrieve("needle", k=1, trace=trace)
    assert before == after
    assert trace["stage_status"]["reranked"] == "error"
    assert trace["errors"]["reranked"] == "RuntimeError"
    assert trace["notes"]
    assert "private provider text" not in json.dumps(trace)


@pytest.mark.parametrize("change", ["corpus", "quote", "pending"])
def test_unusable_labels_do_not_become_false_retrieval_failures(change):
    gold = hit("a", "needle")
    corpus = [gold]
    c = case(gold)
    version = None
    if change == "corpus":
        version = corpus_fingerprint([replace(gold, text="older text")])
    elif change == "quote":
        c["evidence_groups"][0][0]["quote"] = "not present"
    else:
        c["annotation_status"] = "pending"
    row = diagnose(c, {"retrieval": {"stages": {"final": []}}}, corpus, version=version)
    assert row["metrics"] is None
    assert row["failure_stage"] == "labels_unavailable"


def test_cross_paper_groups_require_both_sources():
    a, b = hit("a", "needle fact"), hit("b", "second fact", paper="p2")
    c = case(a)
    c["evidence_groups"].append([reference(b)])
    trace = {"retrieval": {"stages": {"dense": [hit_record(a)], "final": [hit_record(a)]}}}
    row = diagnose(c, trace, [a, b])
    assert row["metrics"]["hit_at_k"]
    assert not row["metrics"]["all_groups_at_k"]
    assert row["metrics"]["label_evidence_recall_at_k"] == 0.5


def test_valid_citation_numbers_do_not_prove_semantic_support_and_reviews_are_bound():
    gold = hit("a", "needle")
    trace = {
        "answer": "Claim [1]",
        "context": [hit_record(gold)],
        "retrieval": {"stages": {"dense": [hit_record(gold)], "final": [hit_record(gold)]}},
    }
    row = diagnose(case(gold), trace, [gold])
    assert row["citation_check"]["invalid_numbers"] == []
    assert row["review"]["citation_supported"] is None
    review = {"artifact_sha256": row["artifact_sha256"], "reviewer": "reader", "citation_supported": False}
    checked = diagnose(case(gold), trace, [gold], review=review)
    assert checked["failure_stage"] == "citation_not_supported"
    changed = deepcopy(trace)
    changed["answer"] = "Different claim [1]"
    stale = diagnose(case(gold), changed, [gold], review=review)
    assert stale["review"]["status"] == "stale_review"
    assert stale["review"]["citation_supported"] is None


def test_nonexistent_citation_numbers_are_detected():
    gold = hit("a", "needle")
    trace = {
        "answer": "Claim [2]",
        "context": [hit_record(gold)],
        "retrieval": {"stages": {"dense": [hit_record(gold)], "final": [hit_record(gold)]}},
    }
    row = diagnose(case(gold), trace, [gold])
    assert row["citation_check"]["invalid_numbers"] == [2]
    assert row["failure_stage"] == "invalid_citation_number"


def test_query_rewrite_and_reported_chat_usage_are_observed():
    gold = hit("a", "needle")
    retriever = Retriever(Store([gold]), cfg={"retrieval": {"mode": "bm25"}})
    replies = iter(
        [
            ("needle", {"prompt_tokens": 3, "completion_tokens": 1}),
            ("Fact [1]", {"prompt_tokens": 5, "completion_tokens": 2}),
        ]
    )
    trace = {}
    answer, hits = answer_question(
        "它是什么？",
        retriever=retriever,
        history=[("needle?", "a fact")],
        chat_fn=lambda *a, **kw: next(replies),
        trace=trace,
    )
    assert answer == "Fact [1]" and hits == [gold]
    assert trace["retrieval_query"] == "needle"
    assert trace["rewrite"] == "applied"
    assert [call["phase"] for call in trace["calls"]] == ["rewrite", "answer"]
    assert sum(call["usage"]["prompt_tokens"] for call in trace["calls"]) == 8


def test_snapshot_tampering_and_pending_labels(tmp_path):
    gold = hit("a", "needle")
    path = tmp_path / "snapshot.json"
    write_snapshot(path, [gold])
    assert load_snapshot(path) == [gold]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hits"][0]["text"] = "modified"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        load_snapshot(path)
    pending = {"id": "unknown", "question": "未知问题", "annotation_status": "pending", "should_answer": None}
    path.write_text(json.dumps(dataset([gold], [pending])), encoding="utf-8")
    assert load_cases(path)["cases"][0]["annotation_status"] == "pending"


def test_reviews_reject_string_booleans_and_empty_judgments(tmp_path):
    path = tmp_path / "review.json"
    base = {"case_id": "q1", "artifact_sha256": "digest", "reviewer": "reader"}
    for fields in ({"answer_correct": "false"}, {}):
        path.write_text(json.dumps({"reviews": [{**base, **fields}]}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_reviews(path)


def test_cli_rejects_implicit_model_calls_and_offline_report_excludes_credentials(tmp_path, monkeypatch):
    runner = CliRunner()
    blocked = runner.invoke(app, ["eval-evidence", "--mode", "vector"])
    assert blocked.exit_code == 2
    blocked_answers = runner.invoke(app, ["eval-evidence", "--with-answers"])
    assert blocked_answers.exit_code == 2
    gold = hit("a", "needle")
    snapshot, data, output = (tmp_path / f"{name}.json" for name in ("snapshot", "cases", "report"))
    write_snapshot(snapshot, [gold])
    data.write_text(json.dumps(dataset([gold], [case(gold)])), encoding="utf-8")
    monkeypatch.setattr("paper_agent.cli.diagnostics.load_config", lambda: {"llm": {"runtime_api_key": "private-key"}})
    result = runner.invoke(
        app, ["eval-evidence", "--dataset", str(data), "--snapshot", str(snapshot), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["cases_scored"] == 1
    assert "private-key" not in output.read_text(encoding="utf-8")
    assert output.with_suffix(".md").is_file()
    unsafe = runner.invoke(app, ["eval-evidence", "--dataset", str(data), "--output", str(data)])
    assert unsafe.exit_code == 2


def test_report_does_not_turn_missing_review_into_accuracy(tmp_path):
    gold = hit("a", "needle")
    r = Retriever(Store([gold]), cfg={"retrieval": {"mode": "bm25"}})
    report = run_diagnostics(dataset([gold], [case(gold)]), [gold], r)
    path = tmp_path / "report.json"
    write_report(path, report)
    assert report["reviewed_answers"] == 0
    assert "answer_accuracy" not in report
    assert "未复核" in path.with_suffix(".md").read_text(encoding="utf-8")


def test_saved_answers_can_be_reviewed_without_model_calls(tmp_path, monkeypatch):
    gold = hit("a", "needle")
    retriever = Retriever(Store([gold]), cfg={"retrieval": {"mode": "bm25"}})
    report = run_diagnostics(
        dataset([gold], [case(gold)]),
        [gold],
        retriever,
        with_answers=True,
        chat_fn=lambda *args, **kw: ("Fact [1]", {}),
    )
    review = {
        "case_id": "q1",
        "artifact_sha256": report["cases"][0]["artifact_sha256"],
        "reviewer": "test reader",
        "answer_correct": False,
    }
    source, review_path, output = (tmp_path / f"{name}.json" for name in ("source", "reviews", "reviewed"))
    write_report(source, report)
    review_path.write_text(json.dumps({"reviews": [review]}), encoding="utf-8")
    monkeypatch.setattr("paper_agent.cli.diagnostics.load_config", lambda: pytest.fail("Review must not load a model"))
    result = CliRunner().invoke(
        app, ["review-evidence", "--report", str(source), "--reviews", str(review_path), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    updated = json.loads(output.read_text(encoding="utf-8"))
    assert updated["cases"][0]["failure_stage"] == "answer_incorrect"
    assert updated["reviewed_answers"] == 1
    assert json.loads(source.read_text(encoding="utf-8"))["reviewed_answers"] == 0
    changed = deepcopy(report)
    changed["cases"][0]["trace"]["answer"] = "Changed claim [1]"
    with pytest.raises(ValueError, match="changed"):
        apply_reviews(changed, {"q1": review})


def test_snapshot_command_preserves_existing_output(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text('{"preserved": true}', encoding="utf-8")
    result = CliRunner().invoke(app, ["evidence-snapshot", "--output", str(path)])
    assert result.exit_code == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"preserved": True}
