"""ChatStore（会话存储）测试：会话 CRUD、消息追加与历史读取。"""

from __future__ import annotations

from paper_agent.chatstore import ChatStore


def test_session_lifecycle(tmp_path):
    store = ChatStore(tmp_path / "lib.db")
    try:
        sid = store.create_session("联邦学习调研")
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == sid
        assert sessions[0]["title"] == "联邦学习调研"
        assert sessions[0]["message_count"] == 0

        assert store.delete_session(sid) is True
        assert store.list_sessions() == []
        assert store.delete_session(sid) is False  # 幂等删除
    finally:
        store.close()


def test_messages_roundtrip_and_order(tmp_path):
    store = ChatStore(tmp_path / "lib.db")
    try:
        sid = store.create_session("会话A")
        store.append_message(sid, role="user", content="帮我找 GNN 论文")
        store.append_message(
            sid, role="assistant", content="已入库 3 篇", intent="search",
            understanding="检索（LLM）", notes=["arxiv 不可用"],
        )
        msgs = store.get_messages(sid)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "帮我找 GNN 论文"
        assert msgs[1]["intent"] == "search"
        assert msgs[1]["notes"] == ["arxiv 不可用"]
        # 会话排在最前（updated_at 最新）
        assert store.list_sessions()[0]["id"] == sid
    finally:
        store.close()


def test_sessions_sorted_by_recency(tmp_path):
    store = ChatStore(tmp_path / "lib.db")
    try:
        old = store.create_session("旧会话")
        new = store.create_session("新会话")
        store.append_message(old, role="user", content="唤醒旧会话")  # old 变为最近活跃
        ids = [s["id"] for s in store.list_sessions()]
        assert ids == [old, new]
    finally:
        store.close()


def test_message_sources_roundtrip(tmp_path):
    """qa 出处随消息持久化，历史读取还原（M11）。"""
    store = ChatStore(tmp_path / "lib.db")
    try:
        sid = store.create_session("问答会话")
        sources = [{"paper_id": "openalex:W1", "title": "T", "section": "methods", "page": 3}]
        store.append_message(sid, role="assistant", content="答案 [1]", sources=sources)
        msgs = store.get_messages(sid)
        assert msgs[0]["sources"] == sources
    finally:
        store.close()


def test_legacy_db_without_sources_column(tmp_path):
    """老库缺 sources 列：打开时自动迁移，历史消息正常读取。"""
    import sqlite3

    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,"
        " intent TEXT, understanding TEXT, notes TEXT, created_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO chat_sessions (id, title, created_at, updated_at)"
        " VALUES ('s1', '旧会话', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, created_at)"
        " VALUES ('s1', 'user', '旧消息', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    store = ChatStore(db)
    try:
        msgs = store.get_messages("s1")
        assert msgs[0]["content"] == "旧消息"
        assert msgs[0]["sources"] == []  # 迁移后新列按空处理
        store.append_message("s1", role="assistant", content="新消息", sources=[{"paper_id": "x"}])
        assert store.get_messages("s1")[1]["sources"] == [{"paper_id": "x"}]
    finally:
        store.close()
