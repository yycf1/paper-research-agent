"""MCP server：把 TOOL_REGISTRY 的 9 个工具经 Model Context Protocol 暴露给任意
MCP host（Claude Desktop / Cursor 等），传输为 stdio，`pa mcp` 启动。

设计约束（M22）：
- 复用而非重写：工具描述直接取自 TOOL_REGISTRY——注册表自 M19 起就按 MCP
  规范（description + inputSchema）描述工具，本模块只做协议适配，不维护
  第二份 schema，工具层改动自动反映到协议层；
- 执行器复用 ToolExecutor：与 agent loop / Web 同一份实现，四态结果语义、
  硬护栏（add_cap / 文件名白名单 / 独立进程超时）原样生效；
- HITL 确认插座的跨协议映射（M27）：优先走 MCP 2026-07-28 的 input_required
  流——confirm 首次触发时返回 InputRequiredResult（内嵌 elicitation/create
  表单请求），宿主（如 Claude Desktop）弹真实确认界面，客户端带着
  input_responses + request_state 重试原请求，服务端按人的决定放行/拒绝；
  PAPER_AGENT_CONFIRM=approve|reject 环境变量是显式运维开关，也是不支持
  input_required 的老宿主的逃生口。硬护栏不受开关影响——协议层升级交互
  方式，不放宽护栏；
- 同步执行器跑在线程池（SQLite/HTTP/子进程都是阻塞调用），并串行化：
  add_papers 依赖「最近一次 search_papers 预览」这一会话状态，并发交叉
  会把 A 的候选喂给 B。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from contextvars import ContextVar
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

CONFIRM_KEY = "confirm"


class _NeedUserInput(BaseException):
    """confirm 触发时当前请求还没有人工响应：不能静默放行，升级为
    InputRequiredResult 让宿主弹真实确认界面，客户端按 2026-07-28 规范
    带着 input_responses + request_state 重试原请求。

    继承 BaseException 是刻意的：这是协议层控制流信号（与 asyncio 的
    CancelledError 同类），必须穿透 ToolExecutor→run_tool 的四态异常
    分类（except Exception 系），否则会被误判成 degraded 工具结果；
    本模块在 _run_tool 里就地接住，不会外泄到协议层之外。
    """

    def __init__(self, count: int, titles: list[str]):
        super().__init__(f"{count} 项操作待确认")
        self.count = count
        self.titles = titles


# 本次工具调用携带的人工响应（重试请求 params.input_responses）。confirm
# 回调跑在 to_thread 的工作线程里，在 _blocking 内 set、结束 reset——随线程
# 上下文传递给回调，而不是挂在 executor 单例上，避免请求间串味。
_input_responses: ContextVar[dict[str, Any] | None] = ContextVar(
    "pa_mcp_input_responses", default=None
)


def _audit(line: str) -> None:
    sys.stderr.write(f"[paper-agent-mcp] {line}\n")
    sys.stderr.flush()


def confirm_gate(count: int, titles: list[str]) -> bool:
    """HITL 插座的 MCP 语境映射（M27 起三段优先级）：

    1. PAPER_AGENT_CONFIRM=approve|reject 显式设置 → 直接决断。运维开关，
       也是不支持 input_required 的老宿主的逃生口；
    2. 未设置且请求里没有人工响应 → 抛 _NeedUserInput，由 _run_tool 捕获
       后返回 InputRequiredResult（宿主弹窗），绝不静默放行；
    3. 重试带响应 → 按人的决定走：accept 放行；decline/cancel 一律拒绝；
       表单里 approve=false 即便 action=accept 也否决（尊重表单内容）。
    """
    head = titles[0] if titles else ""
    mode = os.environ.get("PAPER_AGENT_CONFIRM", "").strip().lower()
    if mode:
        allowed = mode != "reject"
        _audit(
            f"HITL: {count} 项操作待确认（{head[:60]}{'…' if len(head) > 60 else ''}）"
            f"→ PAPER_AGENT_CONFIRM={mode}，{'放行' if allowed else '拒绝'}"
        )
        return allowed
    responses = _input_responses.get()
    if responses is None:
        _audit(f"HITL: {count} 项操作待确认（{head[:60]}）→ 无人工响应，升级 input_required")
        raise _NeedUserInput(count, titles)
    result = responses.get(CONFIRM_KEY)
    if not isinstance(result, t.ElicitResult):
        _audit("HITL: 重试未携带确认响应 → 按拒绝处理")
        return False
    allowed = result.action == "accept"
    if allowed and isinstance(result.content, dict) and result.content.get("approve") is False:
        allowed = False
    _audit(
        f"HITL: elicitation action={result.action}（{head[:60]}）→ {'放行' if allowed else '拒绝'}"
    )
    return allowed


def _pack_state(tool: str, arguments: dict[str, Any] | None) -> str:
    """待确认请求的归属证明：客户端重试时原样回传，服务端核对响应归属。"""
    return json.dumps({"tool": tool, "arguments": arguments or {}}, ensure_ascii=False)


def _state_tool(request_state: str | None) -> str | None:
    try:
        obj = json.loads(request_state or "")
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    tool = obj.get("tool")
    return tool if isinstance(tool, str) else None


def _confirm_request(count: int, titles: list[str]) -> t.ElicitRequest:
    head = "、".join(x[:40] for x in titles[:3])
    if len(titles) > 3:
        head += f" 等 {len(titles)} 项"
    return t.ElicitRequest(
        method="elicitation/create",
        params=t.ElicitRequestFormParams(
            message=f"paper-agent 请求执行确认（{count} 项）：{head}",
            requested_schema={
                "type": "object",
                "properties": {
                    "approve": {
                        "type": "boolean",
                        "description": "true=放行执行；false=拒绝",
                    }
                },
                "required": ["approve"],
            },
        ),
    )


def _error_result(name: str, error: str) -> t.CallToolResult:
    payload = {"tool": name, "status": "fatal", "error": error}
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        is_error=True,
    )


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


async def _run_tool(
    name: str,
    arguments: dict[str, Any] | None,
    responses: dict[str, Any] | None = None,
    request_state: str | None = None,
) -> t.CallToolResult | t.InputRequiredResult:
    if responses is not None and _state_tool(request_state) != name:
        # 重试完整性校验：人工响应必须归属本工具的待确认请求（request_state
        # 是 _pack_state 开出的证明）。响应缺失按拒绝、归属不明按 fatal。
        return _error_result(
            name, "request_state 缺失或与当前工具不匹配：拒绝套用人工确认响应，请重新发起本次调用"
        )

    def _blocking() -> dict[str, Any]:
        token = _input_responses.set(responses)
        try:
            # 注意锁顺序：_get_executor 内部只保惰性初始化；会话状态串行化在
            # execute 一段——两段不可嵌套（threading.Lock 非重入，嵌套即自死锁）
            executor = _get_executor()
            with _confirm_lock:
                raw = json.dumps(arguments or {}, ensure_ascii=False)
                return result_payload(executor.execute(name, raw))
        finally:
            _input_responses.reset(token)

    try:
        payload = await anyio.to_thread.run_sync(_blocking)
    except _NeedUserInput as need:
        return t.InputRequiredResult(
            input_requests={CONFIRM_KEY: _confirm_request(need.count, need.titles)},
            request_state=_pack_state(name, arguments),
        )
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


async def _call_tool(
    ctx: Any, params: t.CallToolRequestParams
) -> t.CallToolResult | t.InputRequiredResult:
    if params.name not in TOOL_REGISTRY:
        payload = {"tool": params.name, "status": "fatal", "error": f"未知工具：{params.name}"}
        return t.CallToolResult(
            content=[t.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            is_error=True,
        )
    return await _run_tool(
        params.name,
        params.arguments,
        responses=params.input_responses,
        request_state=params.request_state,
    )


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
