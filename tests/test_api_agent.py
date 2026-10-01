"""Agent 任务 API 测试：TestClient + 假模型脚本，全离线。

覆盖：提交/轮询端点、步骤事件增量可见、HITL 确认门的同意与拒绝路径、
非等待态确认的 409。LLM 经 monkeypatch paper_agent.llm.chat_with_tools
注入脚本化假模型（循环层默认 chat_fn 走该模块属性，运行时查找可替换）。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from paper_agent.library import Library
from paper_agent.llm import ChatResponse, ToolCall
from paper_agent.models import Paper
from paper_agent.server.app import create_app
from paper_agent.server import routes as rt


def _paper(sid: str, title: str) -> Paper:
    return Paper(source="openalex", source_id=sid, title=title, abstract="摘要",
                 year=2024, citations=3, pdf_urls=["http://x/a.pdf"])


class _FakeStore:
    def __init__(self, path):
        pass

    def count(self):
        return 0  # 库内索引为空：retriever 为 None，ask_library 走降级


def _cfg(tmp_path) -> dict:
    return {
        "data_dir": str(tmp_path / "data"),
        "proxy": "",
        "sources": {},
        "agent": {"confirm_threshold": 1},  # 任何入库都触发确认门（便于测试）
    }


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(rt, "load_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(rt, "VectorStore", _FakeStore)
    monkeypatch.setattr("paper_agent.server.agent_jobs.VectorStore", _FakeStore)
    return TestClient(create_app())


def _tool_resp(name: str, args_json: str, call_id: str) -> ChatResponse:
    return ChatResponse(content=None, tool_calls=[ToolCall(id=call_id, name=name, arguments=args_json)],
                        usage={"prompt": 100, "completion": 20, "model": "fake"})


def _final(text: str) -> ChatResponse:
    return ChatResponse(content=text, tool_calls=[], usage={"prompt": 100, "completion": 20, "model": "fake"})


def _wait_status(client: TestClient, tid: str, statuses: set[str], timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/agent/tasks/{tid}").json()
        if data["status"] in statuses:
            return data
        time.sleep(0.05)
    pytest.fail(f"任务未在 {timeout}s 内进入 {statuses}，当前：{data['status']}")


_PREVIEW = None  # 由 fixture 注入（避免模块级导入循环）


def _patch_search(monkeypatch, papers):
    from paper_agent.agent import executor as executor_mod
    from paper_agent.services.search import Candidate, PreviewResult

    preview = PreviewResult(candidates=[Candidate(paper=p) for p in papers], total_hits=len(papers))
    monkeypatch.setattr(executor_mod, "search_sources", lambda *a, **kw: preview)


def test_agent_task_submit_poll_done(monkeypatch, tmp_path):
    """提交 → 步骤事件增量可见 → done；usage 与答案齐全。"""
    client = _client(monkeypatch, tmp_path)

    script = [
        _tool_resp("library_status", "{}", "c1"),
        _final("（收尾）"),
    ]

    def fake_chat(messages, *, tools, **_):
        if not tools:
            return _final("（收尾）")
        return script.pop(0) if script else _final("（兜底）")

    monkeypatch.setattr("paper_agent.llm.chat_with_tools", fake_chat)

    r = client.post("/api/agent/tasks", json={"task": "看看库状态"})
    assert r.status_code == 202
    tid = r.json()["id"]
    assert r.json()["status"] == "running"

    data = _wait_status(client, tid, {"done"})
    assert data["step_count"] == 1
    assert data["steps"][0]["tool"] == "library_status"
    assert data["steps"][0]["status"] == "ok"
    assert data["answer"] == "（收尾）"
    assert data["stopped_reason"] == "final"
    assert data["usage"]["prompt"] == 200
    assert data["pending_confirm"] is None

    # 404：不存在的任务
    assert client.get("/api/agent/tasks/nonexistent").status_code == 404


def test_agent_task_confirm_gate_approve(monkeypatch, tmp_path):
    """确认门（同意）：waiting_confirm 可见 pending_confirm → /confirm → 入库完成。"""
    client = _client(monkeypatch, tmp_path)
    papers = [_paper("openalex:W1", "Paper One"), _paper("openalex:W2", "Paper Two")]
    _patch_search(monkeypatch, papers)

    script = [
        _tool_resp("search_papers", '{"query": "gnn"}', "c1"),
        _tool_resp("add_papers", '{"pick": "all"}', "c2"),
    ]

    def fake_chat(messages, *, tools, **_):
        if not tools:
            return _final("（收尾）")
        return script.pop(0) if script else _final("（兜底）")

    monkeypatch.setattr("paper_agent.llm.chat_with_tools", fake_chat)

    tid = client.post("/api/agent/tasks", json={"task": "找论文入库"}).json()["id"]
    data = _wait_status(client, tid, {"waiting_confirm", "done"})
    assert data["status"] == "waiting_confirm"
    assert data["pending_confirm"]["count"] == 2
    assert len(data["pending_confirm"]["titles"]) == 2

    # 未决断时重复确认也安全（第二次已是 running → 409）
    r = client.post(f"/api/agent/tasks/{tid}/confirm", json={"approve": True})
    assert r.status_code == 200

    data = _wait_status(client, tid, {"done"})
    add_steps = [s for s in data["steps"] if s["tool"] == "add_papers"]
    assert add_steps and add_steps[0]["status"] == "ok"

    lib = Library(tmp_path / "data" / "library.db")
    try:
        sids = [rec.paper.source_id for rec in lib.list()]
        assert "openalex:W1" in sids and "openalex:W2" in sids
    finally:
        lib.close()


def test_agent_task_confirm_gate_decline(monkeypatch, tmp_path):
    """确认门（拒绝）：不入库，任务正常收尾（模型收到 declined 说明）。"""
    client = _client(monkeypatch, tmp_path)
    papers = [_paper("openalex:W3", "Paper Three")]
    _patch_search(monkeypatch, papers)

    script = [
        _tool_resp("search_papers", '{"query": "gnn"}', "c1"),
        _tool_resp("add_papers", '{"pick": "1"}', "c2"),
    ]

    def fake_chat(messages, *, tools, **_):
        if not tools:
            return _final("（收尾）")
        return script.pop(0) if script else _final("（兜底）")

    monkeypatch.setattr("paper_agent.llm.chat_with_tools", fake_chat)

    tid = client.post("/api/agent/tasks", json={"task": "找论文入库"}).json()["id"]
    _wait_status(client, tid, {"waiting_confirm"})
    r = client.post(f"/api/agent/tasks/{tid}/confirm", json={"approve": False})
    assert r.status_code == 200

    data = _wait_status(client, tid, {"done"})
    lib = Library(tmp_path / "data" / "library.db")
    try:
        assert len(lib.list()) == 0  # 拒绝后未入库
    finally:
        lib.close()


def test_agent_confirm_on_running_task_409(monkeypatch, tmp_path):
    """非等待态任务上的确认 → 409。"""
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/agent/tasks/nonexistent/confirm", json={"approve": True})
    assert r.status_code == 409


def test_agent_tasks_list_and_history_readback(monkeypatch, tmp_path):
    """任务列表：live 优先；清空注册表（模拟重启）后由轨迹文件回读历史。"""
    client = _client(monkeypatch, tmp_path)
    script = [_tool_resp("library_status", "{}", "c1")]

    def fake_chat(messages, *, tools, **_):
        if not tools:
            return _final("（收尾）")
        return script.pop(0) if script else _final("（收尾）")

    monkeypatch.setattr("paper_agent.llm.chat_with_tools", fake_chat)
    tid = client.post("/api/agent/tasks", json={"task": "看看库状态"}).json()["id"]
    _wait_status(client, tid, {"done"})

    # 完成后：live 条目存在，且轨迹文件名含任务 id（重启后回读的关联键）
    tasks = client.get("/api/agent/tasks").json()["tasks"]
    mine = [t for t in tasks if t["id"] == tid]
    assert mine and mine[0]["live"] is True and mine[0]["status"] == "done"

    from paper_agent.server import agent_jobs as aj

    aj.registry._tasks.clear()  # 模拟服务重启（注册表在内存中，轨迹在磁盘上）
    tasks = client.get("/api/agent/tasks").json()["tasks"]
    mine = [t for t in tasks if t["id"] == tid]
    assert mine and mine[0]["live"] is False and mine[0]["step_count"] == 1

    detail = client.get(f"/api/agent/tasks/{tid}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["live"] is False
    assert d["steps"][0]["tool"] == "library_status"
    assert d["answer"] == "（收尾）"
    assert client.get("/api/agent/tasks/nonexistent").status_code == 404


def test_agent_events_stream(monkeypatch, tmp_path):
    """SSE 事件流：snapshot 起始、终态后 end 收尾，content-type 为 text/event-stream。"""
    client = _client(monkeypatch, tmp_path)
    script = [_tool_resp("library_status", "{}", "c1")]

    def fake_chat(messages, *, tools, **_):
        if not tools:
            return _final("（收尾）")
        return script.pop(0) if script else _final("（收尾）")

    monkeypatch.setattr("paper_agent.llm.chat_with_tools", fake_chat)
    tid = client.post("/api/agent/tasks", json={"task": "看看库状态"}).json()["id"]
    _wait_status(client, tid, {"done"})

    events = []
    with client.stream("GET", f"/api/agent/tasks/{tid}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    types = [e["type"] for e in events]
    assert types[0] == "snapshot"
    assert events[0]["status"] == "done"  # 连接时任务已完成：快照即终态
    assert types[-1] == "end"


def test_agent_events_stream_incremental(monkeypatch, tmp_path):
    """SSE 增量：连接时任务仍在第 2 步上等待，放行后应收到 step/status 事件推进到 end。"""
    import threading

    client = _client(monkeypatch, tmp_path)
    release = threading.Event()
    calls = {"n": 0}

    def fake_chat(messages, *, tools, **_):
        if not tools:
            return _final("（收尾）")
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_resp("library_status", "{}", "c1")
        release.wait(timeout=10)  # 第 2 次调用先卡住：给 SSE 连接留出 running 窗口
        return _tool_resp("explore_directions", "{}", "c2")

    monkeypatch.setattr("paper_agent.llm.chat_with_tools", fake_chat)
    tid = client.post("/api/agent/tasks", json={"task": "看看库状态"}).json()["id"]

    import time as _time

    deadline = _time.time() + 5
    while _time.time() < deadline:
        if client.get(f"/api/agent/tasks/{tid}").json()["step_count"] >= 1:
            break
        _time.sleep(0.02)

    events = []
    with client.stream("GET", f"/api/agent/tasks/{tid}/events") as r:
        assert r.status_code == 200
        release.set()
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    types = [e["type"] for e in events]
    assert types[0] == "snapshot"
    assert any(e["type"] == "step" and e["step"]["tool"] == "explore_directions" for e in events)
    assert any(e["type"] == "status" and e["state"]["status"] == "done" for e in events)
    assert types[-1] == "end"
