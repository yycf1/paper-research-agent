"""Offline regressions for host scope, side effects and retrieval boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from paper_agent.agent.executor import ToolExecutor
from paper_agent.agent.loop import run_agent_task
from paper_agent.agent.scope import PaperScope
from paper_agent.library import Library
from paper_agent.llm import ChatResponse
from paper_agent.models import Paper
from paper_agent.rag.qa import Retriever, answer_question
from paper_agent.rag.vectorstore import Hit, VectorStore
from paper_agent.server.routes import _history_task


@pytest.fixture
def context(tmp_path):
    lib = Library(tmp_path / "library.db")
    for n in range(1, 4):
        sid = f"openalex:W{n}"
        lib.upsert_paper(Paper(source="openalex", source_id=sid, title=f"Paper {n}"))
        lib.set_status(sid, "indexed")
    cfg = {"data_dir": str(tmp_path / "data"), "agent": {}}
    yield lib, cfg
    lib.close()


@pytest.mark.parametrize("tool,args", [
    ("read_repro_plan", {"source_id": "W2"}),
    ("write_code", {"source_id": "openalex:W2", "filename": "x.py", "content": "print(1)"}),
    ("run_code", {"source_id": "openalex:W2", "filename": "x.py"}),
    ("ask_library", {"question": "method", "paper_id": "W2"}),
    ("compare_papers", {"topic": "method", "paper_ids": ["W1", "W2"]}),
])
def test_anchor_rejects_cross_paper_before_any_io(context, tmp_path, tool, args):
    lib, cfg = context
    ex = ToolExecutor(lib=lib, cfg=cfg, anchor="W1")
    result = ex.execute(tool, json.dumps(args))
    assert result.status == "fatal" and "作用域" in result.error
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("values", [[], "W1", [None], [""], ["W1"] * 7, ["missing"]])
def test_invalid_host_scope_rejected(context, values):
    lib, cfg = context
    with pytest.raises(ValueError):
        ToolExecutor(lib=lib, cfg=cfg, allowed_paper_ids=values)


def test_scope_conflict_and_ambiguous_alias(context):
    lib, _ = context
    with pytest.raises(ValueError, match="冲突"):
        PaperScope(lib, anchor="W1", allowed_paper_ids=["W1", "W2"])
    lib.upsert_paper(Paper(source="local", source_id="local:W1", title="Duplicate suffix"))
    with pytest.raises(ValueError, match="匹配多篇"):
        PaperScope(lib, allowed_paper_ids=["W1"])


@pytest.mark.parametrize("args", [{"paper_ids": []}, {"paper_ids": "W1"},
                                  {"paper_id": "W1", "paper_ids": ["W1"]}])
def test_ask_invalid_filter_cannot_fall_back_to_global(context, args):
    lib, cfg = context
    ex = ToolExecutor(lib=lib, cfg=cfg, anchor="W1")
    assert ex.execute("ask_library", json.dumps({"question": "method", **args})).status == "fatal"


def test_default_ask_and_compare_use_host_scope(context, monkeypatch):
    lib, cfg = context
    seen = []

    def retrieve(query, *, k, paper_id):
        seen.append(paper_id)
        return [Hit(paper_id, "T", "Method", 1, "graph", 0.1, chunk_id=paper_id + "::0")]

    retriever = SimpleNamespace(store=SimpleNamespace(count=lambda: 3), retrieve=retrieve)
    ex = ToolExecutor(lib=lib, cfg=cfg, retriever=retriever, allowed_paper_ids=["W1", "W2"])

    def answer(question, *, retriever, paper_ids, trace):
        assert paper_ids == ["openalex:W1", "openalex:W2"]
        return "graph [1]", []

    monkeypatch.setattr("paper_agent.agent.executor.answer_question", answer)
    assert ex.execute("ask_library", '{"question":"method"}').ok
    comparison = ex.execute("compare_papers", '{"topic":"method"}')
    assert comparison.ok
    assert seen == ["openalex:W1", "openalex:W2"]
    assert ex.execute("library_status", "{}").data["total"] == 2


def test_scope_persists_in_prompt_trace_and_history(context, tmp_path):
    lib, cfg = context
    ex = ToolExecutor(lib=lib, cfg=cfg, allowed_paper_ids=["W1", "W2"])

    def chat(messages, **kwargs):
        assert "openalex:W1, openalex:W2" in messages[0]["content"]
        assert "openalex:W3" not in messages[0]["content"]
        return ChatResponse(content="done", tool_calls=[], usage={})

    trace = tmp_path / "20261014-120000-scope.jsonl"
    run_agent_task("compare", executor=ex, cfg=cfg, chat_fn=chat, trace_path=trace)
    expected = ["openalex:W1", "openalex:W2"]
    assert json.loads(trace.read_text(encoding="utf-8").splitlines()[0])["allowed_paper_ids"] == expected
    assert _history_task(trace, with_steps=True)["allowed_paper_ids"] == expected


def test_history_keeps_comparison_evidence(tmp_path):
    trace = tmp_path / "20261014-120000-evidence.jsonl"
    comparison = {"papers": [{"paper_id": "p1", "field_evidence": {"metrics": {"status": "unverified"}}}]}
    records = [{"type": "task", "task": "compare"},
               {"type": "step", "step": 1, "tool": "compare_papers", "comparison": comparison},
               {"type": "final", "answer": "done", "stopped_reason": "final"}]
    trace.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    assert _history_task(trace, with_steps=True)["steps"][0]["comparison"] == comparison


def test_both_retrieval_branches_filter_multiple_papers(tmp_path):
    store = VectorStore(tmp_path / "db")
    for pid in ("p1", "p2", "p3"):
        store.upsert_paper_chunks(pid, [pid + " graph attention"],
                                 [{"paper_id": pid, "section": "Method", "page": 1}], [[1.0, 0.0]])
    for mode in ("vector", "bm25", "hybrid"):
        retriever = Retriever(store, embed_fn=lambda _: [[1.0, 0.0]], cfg={"retrieval": {"mode": mode}})
        trace = {}
        hits = retriever.retrieve("graph", k=3, paper_ids=["p1", "p2"], trace=trace)
        assert {h.paper_id for h in hits} == {"p1", "p2"}
        assert trace["paper_ids"] == ["p1", "p2"]
        for stage in trace["stages"].values():
            assert all(h["paper_id"] in {"p1", "p2"} for h in stage)
    with pytest.raises(ValueError):
        store.all_hits(paper_ids=[])
    with pytest.raises(ValueError):
        store.query([1.0, 0.0], paper_id="p1", paper_ids=["p2"])


def test_scope_checked_after_rerank_and_for_precomputed_hits():
    h = Hit("p1", "T", "Method", 1, "graph", 0.1, chunk_id="p1::0")
    store = SimpleNamespace(all_hits=lambda **_: [h], query=lambda *a, **_: [h])
    retriever = Retriever(store, embed_fn=lambda _: [[1.0, 0.0]],
                          cfg={"reranker": {"enabled": True}},
                          rerank_fn=lambda *a, **_: [replace(h, paper_id="p3")])
    with pytest.raises(RuntimeError, match="范围之外"):
        retriever.retrieve("graph", paper_ids=["p1", "p2"])
    with pytest.raises(RuntimeError, match="范围之外"):
        answer_question("method", retriever=retriever, paper_ids=["p2"], hits=[h],
                        chat_fn=lambda *a, **kw: pytest.fail("不得把越界证据发给模型"))
