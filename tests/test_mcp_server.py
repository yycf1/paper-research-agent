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


async def _with_session(fn):
    """建内存流 + 服务端任务 + 已握手的客户端会话，fn(session) 的返回值向上传。"""
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
                await session.initialize()
                result = await fn(session)
            tg.cancel_scope.cancel()
    return result


def _session(fn):
    return anyio.run(lambda: _with_session(fn))


def _payload(result) -> dict:
    assert result.content and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def test_initialize_and_list_tools(mcp_env):
    async def run(session):
        info = await session.initialize()
        tools = await session.list_tools()
        return info, tools

    info, tools = _session(run)
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
    assert mcp_server.confirm_gate(5, ["t1"]) is True  # 缺省放行（审计日志走 stderr）
