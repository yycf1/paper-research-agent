"""Unified conversation regressions: persistence, scope and explicit side effects."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paper_agent.chatstore import ChatStore
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.server import routes as rt
from paper_agent.server.app import create_app
from paper_agent.services.assistant import AssistantReply


@pytest.fixture
def context(monkeypatch, tmp_path):
    cfg = {"data_dir": str(tmp_path / "data"), "sources": {}}
    monkeypatch.setattr(rt, "load_config", lambda: cfg)
    monkeypatch.setattr(rt, "_retriever", lambda: None)
    lib = Library(tmp_path / "data" / "library.db")
    for n in range(1, 4):
        lib.upsert_paper(Paper(source="local", source_id=f"local:P{n}", title=f"Original study {n}"))
    lib.close()
    seen = []
    def handle(text, **kw):
        seen.append(kw)
        return AssistantReply("qa", "Original answer [1]", sources=[{"paper_id": "local:P1", "n": [1]}],
                              telemetry={"call_count": 2}, answer_status="generated")
    monkeypatch.setattr(rt, "handle_message", handle)
    return TestClient(create_app()), seen, tmp_path / "data" / "library.db"


def test_scope_persists_across_requests_restart_and_explicit_reset(context):
    client, seen, db = context
    reply = client.post("/api/chat", json={"message": "methods", "paper_ids": ["P1", "P2"]})
    assert reply.status_code == 200
    sid = reply.json()["session_id"]
    client.post("/api/chat", json={"message": "their evaluation?", "session_id": sid})
    assert seen[-1]["paper_ids"] == ["local:P1", "local:P2"]
    assert seen[-1]["history"] == [("methods", "Original answer [1]")]
    assert seen[-1]["allow_ingest"] is False
    store = ChatStore(db)
    assert store.get_scope(sid) == ["local:P1", "local:P2"]
    store.close()
    restored = client.get(f"/api/sessions/{sid}/messages").json()
    assert restored["paper_ids"] == ["local:P1", "local:P2"]
    assert restored["messages"][1]["details"]["telemetry"]["call_count"] == 2
    client.post("/api/chat", json={"message": "all topics", "session_id": sid, "paper_ids": None})
    assert seen[-1]["paper_ids"] is None
    assert seen[-1]["history"] == []  # old selected answers cannot supply global claims
    assert client.get(f"/api/sessions/{sid}/messages").json()["paper_ids"] is None


@pytest.mark.parametrize("ids", [[], ["missing"], ["P1"] * 7])
def test_invalid_scope_never_falls_back_to_whole_library(context, ids):
    client, seen, _ = context
    assert client.post("/api/chat", json={"message": "method", "paper_ids": ids}).status_code in {400, 422}
    assert seen == []
    assert client.get("/api/sessions").json()["sessions"] == []


def test_deleted_scoped_paper_rejects_followup(context):
    client, seen, db = context
    sid = client.post("/api/chat", json={"message": "method", "paper_ids": ["P1"]}).json()["session_id"]
    lib = Library(db)
    lib.conn.execute("DELETE FROM papers WHERE source_id = 'local:P1'")
    lib.conn.commit(); lib.close()
    assert client.post("/api/chat", json={"message": "and data?", "session_id": sid}).status_code == 400
    assert len(seen) == 1


def test_compare_persists_then_becomes_followup_context(context, monkeypatch):
    client, seen, _ = context
    calls = []
    result = {"papers": [{"paper_id": "local:P1", "title": "Study A", "field_evidence": {
        "fields": {"datasets": [{"normalized_value": "Original-A"}]}}}], "note": "fixture"}
    monkeypatch.setattr(rt, "api_compare", lambda req, lib: calls.append(req) or result)
    r = client.post("/api/chat", json={"message": "compare data", "action": "compare", "paper_ids": ["P1", "P2"]})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["comparison"] == result
    assert seen == [] and calls[0].paper_ids == ["local:P1", "local:P2"]
    saved = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert saved[1]["details"]["comparison"] == result
    client.post("/api/chat", json={"message": "what about the first?", "session_id": sid})
    assert "Original-A" in seen[-1]["history"][0][1]
    assert seen[-1]["paper_ids"] == ["local:P1", "local:P2"]


@pytest.mark.parametrize("ids", [None, ["P1"], ["P1", "P1"]])
def test_compare_requires_two_distinct_explicit_papers(context, ids):
    client, seen, _ = context
    r = client.post("/api/chat", json={"message": "compare", "action": "compare", "paper_ids": ids})
    assert r.status_code == 400
    assert seen == []


def test_agent_is_explicit_linked_scoped_and_followup_reads_finished_result(context, monkeypatch):
    client, seen, _ = context
    submissions = []
    def submit(task, **kw):
        submissions.append((task, kw))
        return SimpleNamespace(to_dict=lambda **_: {"id": kw["task_id"], "task": task, "status": "done"})
    monkeypatch.setattr(rt.agent_jobs.registry, "submit", submit)
    sid = client.post("/api/chat", json={"message": "save them", "paper_ids": ["P1", "P2"]}).json()["session_id"]
    assert submissions == []  # ordinary messages never invoke the Agent
    assert client.post("/api/chat", json={"message": "execute", "action": "execute"}).status_code == 422
    assert client.post("/api/agent/tasks", json={"task": "do it", "session_id": "missing"}).status_code == 404
    r = client.post("/api/agent/tasks", json={"task": "read methods", "session_id": sid})
    assert r.status_code == 202
    tid = r.json()["id"]
    assert submissions[0][1]["allowed_paper_ids"] == ["local:P1", "local:P2"]
    assert submissions[0][1]["history"] == [("save them", "Original answer [1]")]
    messages = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert messages[-1]["details"]["task_id"] == tid
    monkeypatch.setattr(rt, "api_agent_task", lambda id: {"answer": "Finished original method result [1]"})
    client.post("/api/chat", json={"message": "explain that result", "session_id": sid})
    assert seen[-1]["history"][-1] == ("read methods", "Finished original method result [1]")


def test_failed_compare_does_not_leave_a_dangling_user_message(context, monkeypatch):
    from fastapi import HTTPException
    client, _, _ = context
    sid = client.post("/api/chat", json={"message": "initial"}).json()["session_id"]
    def fail(*a): raise HTTPException(503, "fixture failure")
    monkeypatch.setattr(rt, "api_compare", fail)
    assert client.post("/api/chat", json={"message": "compare", "action": "compare", "paper_ids": ["P1", "P2"], "session_id": sid}).status_code == 503
    saved = client.get(f"/api/sessions/{sid}/messages").json()
    assert len(saved["messages"]) == 2 and saved["paper_ids"] is None


def test_agent_history_supplies_context_without_changing_current_goal(context):
    from paper_agent.agent.executor import ToolExecutor
    from paper_agent.agent.loop import run_agent_task
    from paper_agent.llm import ChatResponse
    _, _, db = context
    lib = Library(db)
    executor = ToolExecutor(lib=lib, cfg={}, allowed_paper_ids=["local:P1"])
    seen = []
    def chat(messages, **kw):
        seen.append(messages[:])
        return ChatResponse("Current request answered.", usage={"prompt": 1, "completion": 1})
    result = run_agent_task("explain it", executor=executor, chat_fn=chat,
                            history=[("old question", "saved method answer")])
    assert result.stopped_reason == "final"
    assert seen[0][-1] == {"role": "user", "content": "explain it"}
    assert seen[0][-2]["content"] == "saved method answer"
    assert "不授权历史操作" in seen[0][0]["content"]
    assert executor.allowed_paper_ids == ("local:P1",)
    lib.close()


def test_direction_suggestion_only_reads_selected_titles(context):
    from paper_agent.services.assistant import suggest_directions
    _, _, db = context
    lib = Library(db)
    lib.upsert_paper(Paper(source="local", source_id="local:outside", title="Outside scope title"))
    seen = []
    suggest_directions(lib, paper_ids=["local:P1", "local:P2", "local:P3"],
                       chat_fn=lambda messages, **kw: (seen.append(messages[0]["content"]) or "direction", {}))
    assert "Outside scope title" not in seen[0]
    assert all(f"Original study {n}" in seen[0] for n in (1, 2, 3))
    lib.close()
