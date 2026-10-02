"""MCP server 测试：SDK 内存流客户端全离线（monkeypatch 外部 IO），协议级断言。

内存传输：create_client_server_memory_streams 产出客户端/服务端两组
(read, write) 流；服务端用被测 server 对象跑 run()，客户端用 ClientSession
走 initialize → list_tools / call_tool 的真实协议握手，而非直接调内部函数。
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from paper_agent import mcp_server
from paper_agent.models import Paper
from paper_agent.tools import TOOL_REGISTRY


class _FakeStore:
    def __init__(self, path):
        pass

    def count(self):
        return 0


@pytest.fixture()
def mcp_env(monkeypatch, tmp_path) -> dict:
    """离线环境：tmp 数据目录 + 空向量库；清掉惰性单例防用例间串状态。"""
    cfg = {"data_dir": str(tmp_path / "data"), "proxy": "", "sources": {}}
    monkeypatch.setattr(mcp_server, "load_config", lambda: cfg)
    monkeypatch.setattr(mcp_server, "VectorStore", _FakeStore)
    mcp_server.reset_executor()
    return cfg


def _seed(cfg: dict, sid: str = "openalex:W1", title: str = "Graph Neural Survey") -> None:
    lib_path = Path(cfg["data_dir"]) / "library.db"
    from paper_agent.library import Library

    lib = Library(lib_path)
    lib.upsert_paper(Paper(source="openalex", source_id=sid, title=title, pdf_urls=["http://x/a.pdf"]))
    lib.close()


async def _with_session(fn, *, modern: bool = True):
    """建内存流 + 服务端任务 + 已协商的客户端会话，fn(session) 的返回值向上传。

    modern=True 走 2026-07-28 的 server/discover 协商——只有现代连接上
    InputRequiredResult 才是 tools/call 的合法结果形状；modern=False 走
    legacy initialize 握手，覆盖老宿主兼容路径。
    """
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        server = mcp_server.server

        async def _serve():
            await server.run(
                server_streams[0], server_streams[1], server.create_initialization_options()
            )

        result = None
        async with anyio.create_task_group() as tg:
            tg.start_soon(_serve)
            async with ClientSession(*client_streams) as session:
                if modern:
                    await session.discover()
                else:
                    await session.initialize()
                result = await fn(session)
            tg.cancel_scope.cancel()
    return result


def _session(fn, **kw):
    return anyio.run(lambda: _with_session(fn, **kw))


def _payload(result) -> dict:
    assert result.content and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def test_initialize_and_list_tools(mcp_env):
    async def run(session):
        info = await session.initialize()
        tools = await session.list_tools()
        return info, tools

    info, tools = _session(run, modern=False)  # legacy 握手：老宿主兼容路径
    assert info.server_info.name == "paper-agent"
    names = [t.name for t in tools.tools]
    assert names == list(TOOL_REGISTRY.keys())  # 注册表顺序即协议暴露顺序
    for t in tools.tools:
        assert t.description
        assert t.input_schema.get("type") == "object"


def test_call_library_status_ok(mcp_env):
    _seed(mcp_env)

    async def run(session):
        return await session.call_tool("library_status", {})

    result = _session(run)
    assert result.is_error is False
    payload = _payload(result)
    assert payload["status"] == "ok"
    assert payload["data"]["total"] == 1


def test_call_unknown_tool_fatal(mcp_env):
    async def run(session):
        return await session.call_tool("definitely_not_a_tool", {})

    result = _session(run)
    assert result.is_error is True
    payload = _payload(result)
    assert payload["status"] == "fatal"
    assert "未知工具" in payload["error"]


def test_error_semantics_flow_through(mcp_env):
    """错误回填语义跨协议保持：无预览就 add_papers → fatal，host 端模型可自纠错。"""
    async def run(session):
        return await session.call_tool("add_papers", {"pick": "1"})

    result = _session(run)
    payload = _payload(result)
    assert payload["status"] == "fatal"
    assert result.is_error is True


def test_confirm_gate_env_switch(monkeypatch):
    monkeypatch.setenv("PAPER_AGENT_CONFIRM", "reject")
    assert mcp_server.confirm_gate(5, ["t1", "t2"]) is False
    monkeypatch.setenv("PAPER_AGENT_CONFIRM", "approve")
    assert mcp_server.confirm_gate(5, ["t1"]) is True
    monkeypatch.delenv("PAPER_AGENT_CONFIRM")
    # M27 起 env 未设置 = 走 elicitation：无人工响应语境下不允许静默放行
    with pytest.raises(mcp_server._NeedUserInput):
        mcp_server.confirm_gate(5, ["t1"])


def test_confirm_gate_elicitation_responses(monkeypatch):
    import mcp.types as t

    monkeypatch.delenv("PAPER_AGENT_CONFIRM", raising=False)
    # 无人工响应 → 升级 input_required（异常里带待确认上下文）
    with pytest.raises(mcp_server._NeedUserInput) as ei:
        mcp_server.confirm_gate(5, ["A survey", "B method"])
    assert ei.value.count == 5
    assert ei.value.titles == ["A survey", "B method"]

    mcp_server._input_responses.set({"confirm": t.ElicitResult(action="accept")})
    try:
        assert mcp_server.confirm_gate(5, ["t1"]) is True
    finally:
        mcp_server._input_responses.set(None)
    for action in ("decline", "cancel"):
        mcp_server._input_responses.set({"confirm": t.ElicitResult(action=action)})
        try:
            assert mcp_server.confirm_gate(5, ["t1"]) is False
        finally:
            mcp_server._input_responses.set(None)
    # accept 但表单里勾了不放行 → 表单内容否决提交动作
    mcp_server._input_responses.set(
        {"confirm": t.ElicitResult(action="accept", content={"approve": False})}
    )
    try:
        assert mcp_server.confirm_gate(5, ["t1"]) is False
    finally:
        mcp_server._input_responses.set(None)
    # 响应字典里没有 confirm 键（宿主取消了请求）→ 失败关闭
    mcp_server._input_responses.set({})
    try:
        assert mcp_server.confirm_gate(5, ["t1"]) is False
    finally:
        mcp_server._input_responses.set(None)


def _write_args() -> dict:
    return {
        "source_id": "openalex:W1",
        "filename": "t.py",
        "content": "print('hello sandbox')",
    }


def _run_args() -> dict:
    return {"source_id": "openalex:W1", "filename": "t.py"}


def test_confirm_elicitation_roundtrip_accept(mcp_env):
    """全回路：首触 input_required → 宿主（测试扮演）accept → 重试真正执行。"""
    import mcp.types as t

    _seed(mcp_env)

    async def run(session):
        w_args, r_args = _write_args(), _run_args()
        write = await session.call_tool("write_code", w_args)
        assert write.is_error is False  # write_code 不过确认门
        first = await session.call_tool("run_code", r_args, allow_input_required=True)
        assert isinstance(first, t.InputRequiredResult)
        assert first.result_type == "input_required"
        req = first.input_requests[mcp_server.CONFIRM_KEY]
        assert isinstance(req, t.ElicitRequest)
        assert req.params.mode == "form"
        assert "运行代码" in req.params.message
        assert first.request_state  # 归属证明已随请求下发
        # 宿主弹窗 → 人点确认 → 按 2026-07-28 规范带响应重试原请求
        return await session.call_tool(
            "run_code",
            r_args,
            input_responses={mcp_server.CONFIRM_KEY: t.ElicitResult(action="accept")},
            request_state=first.request_state,
        )

    result = _session(run)
    assert result.is_error is False
    payload = _payload(result)
    assert payload["status"] == "ok"
    assert payload["data"]["ran"] is True
    assert payload["data"]["exit_code"] == 0
    assert "hello sandbox" in payload["data"]["stdout"]


def test_confirm_elicitation_decline(mcp_env):
    """人拒绝 → 重试后 executor 返回 declined 语义（不是协议错误）。"""
    import mcp.types as t

    _seed(mcp_env)

    async def run(session):
        await session.call_tool("write_code", _write_args())
        r_args = _run_args()
        first = await session.call_tool("run_code", r_args, allow_input_required=True)
        return await session.call_tool(
            "run_code",
            r_args,
            input_responses={mcp_server.CONFIRM_KEY: t.ElicitResult(action="decline")},
            request_state=first.request_state,
        )

    result = _session(run)
    payload = _payload(result)
    assert result.is_error is False  # 拒绝是明确的人为决定，模型可据此调整
    assert payload["data"]["declined"] is True
    assert payload["data"]["ran"] is False


def test_confirm_state_mismatch_fatal(mcp_env):
    """响应归属校验：request_state 对不上工具就拒绝套用人工确认。"""
    import mcp.types as t

    _seed(mcp_env)

    async def run(session):
        await session.call_tool("write_code", _write_args())
        r_args = _run_args()
        first = await session.call_tool("run_code", r_args, allow_input_required=True)
        forged = json.dumps({"tool": "add_papers", "arguments": {}}, ensure_ascii=False)
        return await session.call_tool(
            "run_code",
            r_args,
            input_responses={mcp_server.CONFIRM_KEY: t.ElicitResult(action="accept")},
            request_state=forged,
        )

    result = _session(run)
    payload = _payload(result)
    assert result.is_error is True
    assert payload["status"] == "fatal"
    assert "request_state" in payload["error"]


def test_env_override_skips_elicitation(mcp_env, monkeypatch):
    """运维开关优先：env=reject 时直接拒绝，不产生 input_required 往返。"""
    import mcp.types as t

    monkeypatch.setenv("PAPER_AGENT_CONFIRM", "reject")
    _seed(mcp_env)

    async def run(session):
        await session.call_tool("write_code", _write_args())
        return await session.call_tool("run_code", _run_args(), allow_input_required=True)

    result = _session(run)
    payload = _payload(result)
    assert payload["status"] == "ok"
    assert payload["data"]["declined"] is True
