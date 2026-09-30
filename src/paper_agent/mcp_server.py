"""MCP server：把 TOOL_REGISTRY 的 9 个工具经 Model Context Protocol 暴露给任意
MCP host（Claude Desktop / Cursor 等），传输为 stdio，`pa mcp` 启动。

设计约束（M22）：
- 复用而非重写：工具描述直接取自 TOOL_REGISTRY——注册表自 M19 起就按 MCP
  规范（description + inputSchema）描述工具，本模块只做协议适配，不维护
  第二份 schema，工具层改动自动反映到协议层；
- 执行器复用 ToolExecutor：与 agent loop / Web 同一份实现，四态结果语义、
  硬护栏（add_cap / 文件名白名单 / 独立进程超时）原样生效；
- HITL 确认插座的跨协议映射：stdio 场景没有可弹确认框的前端，confirm 按
  环境变量 PAPER_AGENT_CONFIRM=approve|reject 处理（默认 approve 并写
  stderr 审计日志）。硬护栏不受该开关影响——协议适配层降级交互方式，
  不放宽护栏；
- 同步执行器跑在线程池（SQLite/HTTP/子进程都是阻塞调用），并串行化：
  add_papers 依赖「最近一次 search_papers 预览」这一会话状态，并发交叉
  会把 A 的候选喂给 B。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

import anyio
import mcp.types as t
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from paper_agent import paths
from paper_agent.agent import ToolExecutor
from paper_agent.config import load_config
from paper_agent.rag.qa import Retriever
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.tools import TOOL_REGISTRY

INSTRUCTIONS = (
    "paper-agent 文献库工具集：search_papers 主题检索预览、add_papers 挑选入库、"
    "ask_library 库内 RAG 问答（带出处）、library_status 库状态、explore_directions "
    "方向推荐、compare_papers 跨论文证据对比、read_repro_plan/write_code/run_code "
    "复现三件套。工具返回 JSON：status=ok 可用；retryable_error 可按 retry_after "
    "重试；degraded 本次不可用应换路；fatal 参数错误不要原样重试。"
)

_confirm_lock = threading.Lock()
_executor: ToolExecutor | None = None


def confirm_gate(count: int, titles: list[str]) -> bool:
    """HITL 插座的 MCP 语境映射：无前端可交互，按环境变量决断并审计。"""
    mode = os.environ.get("PAPER_AGENT_CONFIRM", "approve").strip().lower()
    allowed = mode != "reject"
    head = titles[0] if titles else ""
    sys.stderr.write(
        f"[paper-agent-mcp] HITL: {count} 项操作待确认（{head[:60]}"
        f"{'…' if len(head) > 60 else ''}）→ PAPER_AGENT_CONFIRM={mode or 'approve'}，"
        f"{'放行' if allowed else '拒绝'}\n"
    )
    sys.stderr.flush()
    return allowed


def _get_executor() -> ToolExecutor:
    """惰性单例：首次工具调用时建库连接与检索器，之后复用会话状态。"""
    global _executor
    with _confirm_lock:
        if _executor is None:
            cfg = load_config()
            lib = paths.library(cfg)
            store = VectorStore(paths.vector_dir(cfg))
            retriever = Retriever(store) if store.count() > 0 else None
            _executor = ToolExecutor(lib=lib, retriever=retriever, cfg=cfg, confirm=confirm_gate)
        return _executor


def reset_executor() -> None:
    """测试钩子：清掉单例，让下一个用例注入自己的 cfg。"""
    global _executor
    with _confirm_lock:
        _executor = None


def result_payload(result) -> dict[str, Any]:
    """ToolResult → 可序列化 dict（四态语义原样透传，不吞状态）。"""
    out: dict[str, Any] = {"tool": result.tool, "status": result.status}
    if result.status == "ok":
        out["data"] = result.data
    else:
        out["error"] = result.error
        if result.retry_after:
            out["retry_after"] = result.retry_after
    return out


async def _run_tool(name: str, arguments: dict[str, Any] | None) -> t.CallToolResult:
    def _blocking() -> dict[str, Any]:
        # 注意锁顺序：_get_executor 内部只保惰性初始化；会话状态串行化在
        # execute 一段——两段不可嵌套（threading.Lock 非重入，嵌套即自死锁）
        executor = _get_executor()
        with _confirm_lock:
            raw = json.dumps(arguments or {}, ensure_ascii=False)
            return result_payload(executor.execute(name, raw))

    try:
        payload = await anyio.to_thread.run_sync(_blocking)
    except Exception as exc:  # 协议层兜底：意外异常按 degraded 语义回填，不让连接崩
        payload = {"tool": name, "status": "degraded", "error": f"{type(exc).__name__}: {exc}"[:500]}
    text = json.dumps(payload, ensure_ascii=False)
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=text)],
        is_error=payload["status"] != "ok",
    )


async def _list_tools(ctx: Any, params: Any) -> t.ListToolsResult:
    tools = [
        t.Tool(
            name=name,
            description=spec.get("description", ""),
            input_schema=spec.get("inputSchema") or {"type": "object", "properties": {}},
        )
        for name, spec in TOOL_REGISTRY.items()
    ]
    return t.ListToolsResult(tools=tools)


async def _call_tool(ctx: Any, params: t.CallToolRequestParams) -> t.CallToolResult:
    if params.name not in TOOL_REGISTRY:
        payload = {"tool": params.name, "status": "fatal", "error": f"未知工具：{params.name}"}
        return t.CallToolResult(
            content=[t.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            is_error=True,
        )
    return await _run_tool(params.name, params.arguments)


server = Server(
    "paper-agent",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


async def _amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options(), raise_exceptions=False
        )


def main() -> None:
    anyio.run(_amain)
