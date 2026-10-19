"""Agent 任务评测的离线测试：判定纯函数 + 脚本化假模型跑通 runner。"""

from __future__ import annotations

import json

import pytest

from paper_agent.agent import executor as executor_mod
from paper_agent.agent.eval_agent import (
    aggregate_results,
    judge_tool_usage,
    load_agent_tasks,
    run_agent_evalset,
)
from paper_agent.agent.executor import ToolExecutor
from paper_agent.library import Library
from paper_agent.llm import ChatResponse, ToolCall
from paper_agent.models import Paper
from paper_agent.services.search import Candidate, PreviewResult


def _tool_resp(name: str, args_json: str, call_id: str = "c1") -> ChatResponse:
    return ChatResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args_json)],
        usage={"prompt": 100, "completion": 20, "model": "fake"},
    )


def _final(text: str) -> ChatResponse:
    return ChatResponse(content=text, tool_calls=[], usage={"prompt": 100, "completion": 20, "model": "fake"})


def test_judge_tool_usage():
    ok, v = judge_tool_usage(["search_papers", "add_papers"], ["search_papers"], ["ask_library"])
    assert ok and not v
    ok, v = judge_tool_usage(["search_papers"], ["search_papers", "add_papers"], [])
    assert not ok and any("缺少期望工具" in s for s in v)
    ok, v = judge_tool_usage(["search_papers", "add_papers"], ["search_papers"], ["add_papers"])
    assert not ok and any("禁止" in s for s in v)  # 越界操作被抓到
    assert judge_tool_usage([], [], [])[0] is None  # 无约束时无法判定


def test_load_agent_tasks(tmp_path):
    data = {"cases": [
        {"id": "a", "kind": "multi_step", "task": "t", "must_call": ["library_status"]},
        {"id": "b", "kind": "boundary", "task": "t2", "forbidden": ["search_papers"]},
    ]}
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    cases = load_agent_tasks(path)
    assert len(cases) == 2

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cases": [{"id": "x", "kind": "wrong", "task": "t"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        load_agent_tasks(bad)


def test_runner_multi_step_task(tmp_path, monkeypatch):
    """multi_step 任务：脚本驱动 检索→入库→回答，三项判定与聚合全绿。"""
    lib = Library(tmp_path / "lib.db")
    try:
        paper = Paper(source="openalex", source_id="openalex:W9", title="GNN Survey",
                      abstract="abs", year=2024, citations=10, pdf_urls=["http://x"])
        preview = PreviewResult(candidates=[Candidate(paper=paper)], total_hits=3)

        script = [
            _tool_resp("search_papers", '{"query": "gnn survey"}', "c1"),
            _tool_resp("add_papers", '{"pick": "1"}', "c2"),
            _final("已入库 GNN Survey。"),
        ]

        def chat_fn(messages, *, tools):
            return script.pop(0) if script and tools else _final("已入库 GNN Survey。")

        monkeypatch.setattr(executor_mod, "search_sources", lambda *a, **kw: preview)

        def factory():
            return ToolExecutor(lib=lib, cfg={"proxy": "", "sources": {}})

        case = {"id": "t1", "kind": "multi_step", "task": "找一篇 GNN 综述入库",
                "must_call": ["search_papers", "add_papers"], "forbidden": ["ask_library"],
                "expect_keywords": [["gnn survey"]],
                "acceptance": [{"kind": "library_add", "min_added": 1}]}
        results = run_agent_evalset([case], executor_factory=factory, chat_fn=chat_fn)
        r = results[0]
        assert r.completed and r.tool_ok and r.answer_ok
        assert r.violations == []

        agg = aggregate_results(results)
        assert agg["completion_rate"] == 1.0
        assert agg["tool_ok_rate"] == 1.0
        assert agg["answer_ok_rate"] == 1.0
        assert agg["violations"] == 0
        assert agg["by_kind"]["multi_step"] == {"total": 1, "completed": 1}
    finally:
        lib.close()


def test_runner_boundary_violation(tmp_path):
    """boundary 任务：假模型违规调工具 → tool_ok=False、violations 非空。"""
    lib = Library(tmp_path / "lib.db")
    try:
        script = [
            _tool_resp("search_papers", '{"query": "weekly report"}', "c1"),
            _final("好的周报写好了。"),
        ]

        def chat_fn(messages, *, tools):
            return script.pop(0) if script and tools else _final("好的周报写好了。")

        def factory():
            return ToolExecutor(lib=lib, cfg={"proxy": "", "sources": {}})

        case = {"id": "b1", "kind": "boundary", "task": "帮我写周报",
                "forbidden": ["search_papers"]}
        results = run_agent_evalset([case], executor_factory=factory, chat_fn=chat_fn)
        r = results[0]
        assert r.tool_ok is False
        assert any("禁止" in s for s in r.violations)

        agg = aggregate_results(results)
        assert agg["violations"] == 1
    finally:
        lib.close()
