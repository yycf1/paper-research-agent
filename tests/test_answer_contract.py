from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from paper_agent.agent.executor import ToolExecutor
from paper_agent.library import Library
from paper_agent.rag.citations import check_citations
from paper_agent.rag.qa import answer_question
from paper_agent.rag.vectorstore import Hit


def retriever(*, count=5, corpus=None):
    return SimpleNamespace(store=SimpleNamespace(count=lambda: count, all_hits=lambda **_: corpus or []),
                           retrieve=lambda *a, **kw: [], cfg={}, notes=[])


@pytest.mark.parametrize("count,scope,corpus,status,phrase", [
    (0, None, [], "empty_index", "pa index"),
    (5, None, [], "no_hits", "未召回"),
    (5, ["p1"], [], "scope_unindexed", "所选论文"),
    (5, ["p1"], [Hit("p1", "T", "M", 1, "text", 0.1)], "no_hits", "未召回"),
])
def test_empty_results_have_distinct_states(count, scope, corpus, status, phrase):
    trace = {}
    kwargs = {"paper_ids": scope} if scope else {}
    answer, hits = answer_question("q", retriever=retriever(count=count, corpus=corpus), trace=trace,
                                   chat_fn=lambda *a, **kw: pytest.fail("零证据不应调用模型"), **kwargs)
    assert phrase in answer and hits == []
    assert trace["answer_status"] == status


@pytest.mark.parametrize("text,size,valid", [
    ("claim [1]", 1, True), ("claim [99]", 1, False), ("claim [0]", 1, False),
    ("claim [-1]", 1, False), ("claim [1, 2]", 2, True), ("claim [1-3]", 2, False),
    ("claim [3–1]", 3, False), ("claim [1-99999]", 2, False),
    ("claim without references", 1, False), ("论文库中的内容未提及：证据不足。", 1, True),
    ("论文库中的内容未提及 [99]", 1, False),
])
def test_citation_structure(text, size, valid):
    checked = check_citations(text, size)
    assert (checked["status"] == "passed") is valid
    assert checked["semantic_support"] == "not_automatically_judged"


@pytest.mark.parametrize("repaired,status", [("supported [1]", "generated"),
                                             ("still invalid [99]", "citation_invalid"),
                                             ("no reference", "citation_invalid")])
def test_bounded_repair_never_relabels_bad_reference(repaired, status):
    calls = []

    def chat(messages, **kw):
        calls.append(messages)
        return ("bad draft [99]" if len(calls) == 1 else repaired), {}

    trace = {}
    hit = Hit("p1", "T", "Method", 1, "direct evidence", 0.1, chunk_id="p1::0")
    answer, hits = answer_question("q", retriever=retriever(), hits=[hit], trace=trace, chat_fn=chat)
    assert len(calls) == 2
    assert hits == [hit] and trace["answer_status"] == status
    assert "[99]" not in answer
    assert trace["citation_attempts"][0]["check"]["invalid_numbers"] == [99]
    if status == "citation_invalid":
        assert "校验未通过" in answer
    else:
        assert answer == repaired


def test_repair_outage_returns_validation_failure_not_bad_draft():
    calls = []

    def chat(*a, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("offline provider failure")
        return "bad [99]", {}

    answer, _ = answer_question("q", retriever=retriever(),
                                hits=[Hit("p1", "T", "M", 1, "evidence", 0.1)], chat_fn=chat)
    assert len(calls) == 2 and "[99]" not in answer and "校验未通过" in answer


def test_ask_service_error_is_degraded_tool_result(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library.db")
    try:
        ex = ToolExecutor(lib=lib, retriever=retriever(), cfg={"data_dir": str(tmp_path), "agent": {}})

        def fail(*a, **kw):
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr("paper_agent.agent.executor.answer_question", fail)
        result = ex.execute("ask_library", json.dumps({"question": "q"}))
        assert result.status == "degraded" and result.data is None
    finally:
        lib.close()
