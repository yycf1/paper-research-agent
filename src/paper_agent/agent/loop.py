"""Function-calling 主循环：模型看到工具注册表 → 自主发起 tool_calls →
执行器跑真实工具 → 结果以 role=tool 消息回填 → 模型继续决策，直到给出
最终回答或触发护栏。

与 M5 意图路由（classify_intent 单次分类 → Python 硬编码分发）的本质区别：
- 路由模式：控制流在代码里，LLM 只产出参数，一次调用一条固定路径；
- 循环模式：控制流在模型手里，它看得到每步工具结果，可以改关键词重搜、
  先检索再入库、检索完顺带就库内内容追问——多步任务的编排由模型自主完成。

护栏四件套（M15，prompt 约束只是建议，这里全是代码强制）：
- max_steps 步数熔断：防止模型无限循环烧 token；触达后强制无工具总结（软着陆）；
- token_budget 预算：步数限制不了单步成本（每步重放全部历史），预算按实际
  用量在下一轮调用前拦截，与 max_steps 并列双闸；
- 循环检测：同 (工具名, 参数) 签名第 2 次执行时回填警告、第 3 次直接拒绝
  执行——重复调用不会产生新结果，早拦早省；
- 错误回填：工具失败不抛给用户，而是作为 status 消息回给模型自行纠正；
  其中 retryable_error（限流）由框架按 retry_after 自动重试一次。
上下文保护：tool 消息超长截断，候选列表封顶（executor 侧）。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from paper_agent import llm
from paper_agent.agent.executor import ToolExecutor
from paper_agent.llm import ChatResponse, assistant_message, tool_message
from paper_agent.tools import ToolResult

DEFAULT_MAX_STEPS = 8
DEFAULT_TOKEN_BUDGET = 120_000
MAX_AUTO_RETRY_WAIT = 30  # 自动重试等待上限（秒），防止 retry_after 异常大阻塞 CLI

SYSTEM_PROMPT = """你是 paper-agent，一个论文研究 Agent。通过调用工具完成用户任务：

- 找新论文：先用 search_papers 检索预览（query 用英文学术表述，可按需要带
  year_from/sort/oa_only 等条件），看到候选后再用 add_papers 挑选入库；
- 已入库论文的内容问题：用 ask_library（它会检索并带出处回答）；
- 多篇论文对比：先用 compare_papers 按对比主题收集各论文的证据块，
  再综合成对比结论并标注出处论文——不要凭记忆对比；
- 复现/原型验证任务：read_repro_plan 理解方案 → write_code 写最小可运行
  原型（依赖仅限标准库与 numpy，用合成数据，不联网）→ run_code 执行；
  exit_code 非 0 或超时就按 stderr 修代码重跑（最多 3 次），成功后汇报
  输出中的关键数字；用户拒绝执行时不要重试；
- 查库状态：library_status；用户没头绪时：explore_directions；
- 与论文研究无关的请求（闲聊、写周报、写代码等）：不要调用任何工具，直接
  说明你的职责范围并引导回科研场景；
- 工具返回 {"status": ...}：ok 正常使用；degraded/retryable_error 说明本次
  没成功，可换参数重试或如实告知用户；fatal 不要用同样的参数重试；
- 检索结果不理想时可调整关键词再试（最多换 2 次），不要无限重试；拿到候选后
  尽快推进任务（入库或回答）；0 结果且提示数据源故障时不要换词重搜，如实告知；
- 相互依赖的调用必须等上一步结果，不要凭空猜测工具会返回什么；
- 信息足够后停止调用工具，用中文给出最终回答：做了什么、结果如何、建议的下一步。"""


@dataclass
class AgentStep:
    """一步工具调用的观测记录（CLI/Web 展示与评测都用它）。"""

    step: int
    tool: str
    arguments: dict = field(default_factory=dict)
    status: str = ""
    detail: str = ""
    duration_ms: int = 0


@dataclass
class AgentRunResult:
    """一次任务的完整结果：最终回答 + 全部步骤轨迹 + token 用量。"""

    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stopped_reason: str = "final"  # final | max_steps | token_budget | error


class _RepeatGuard:
    """循环检测器：签名级 + 工具级两层。

    - 签名级：同 (工具名, 规范化参数) 第 2 次执行带警告、第 3 次拒绝执行；
    - 工具级：同一工具累计调用达 WARN_TOOL_AT 次起，结果中注入推进提醒——
      拦「参数微调式变体循环」（换关键词反复检索但从不推进任务），
      这类循环签名各不相同，只有按工具计数才能覆盖。
    """

    WARN_AT = 2
    BLOCK_AT = 3
    WARN_TOOL_AT = 4

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._tool_counts: dict[str, int] = {}

    @staticmethod
    def signature(tool: str, arguments: str) -> str:
        try:
            parsed = json.loads(arguments) if arguments and arguments.strip() else {}
            canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        except ValueError:
            canonical = (arguments or "")[:200]
        return f"{tool}::{canonical}"

    def check(self, sig: str) -> int:
        """登记并返回本次是该签名的第几次调用（1-based）。"""
        self._counts[sig] = self._counts.get(sig, 0) + 1
        return self._counts[sig]

    def tool_nth(self, tool: str) -> int:
        """登记并返回本次是该工具的第几次调用（1-based）。"""
        self._tool_counts[tool] = self._tool_counts.get(tool, 0) + 1
        return self._tool_counts[tool]

    def tool_warning(self, tool: str, nth: int) -> str:
        return (
            f"这是第 {nth} 次调用 {tool}。如果已获得足够信息，请立即推进任务"
            "（如 add_papers 入库或直接给出最终回答），不要继续同类调用。"
        )


class _TraceWriter:
    """JSONL 轨迹持久化：逐步追加写，中途崩溃也能复盘到已发生的步骤。"""

    def __init__(self, path: Path, task: str, cfg: dict | None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("w", encoding="utf-8")
        self._emit({"type": "task", "task": task, "config": {"agent": (cfg or {}).get("agent", {})}})

    def _emit(self, obj: dict) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._f.flush()

    def step(self, step: AgentStep, retried: bool) -> None:
        self._emit({"type": "step", "retried": retried, **asdict(step)})

    def final(self, result: "AgentRunResult") -> None:
        self._emit({
            "type": "final",
            "stopped_reason": result.stopped_reason,
            "usage": result.usage,
            "steps": len(result.steps),
            "answer": result.answer,
        })
        self._f.close()


def _parse_args_for_display(arguments: str) -> dict:
    try:
        data = json.loads(arguments) if arguments and arguments.strip() else {}
        return data if isinstance(data, dict) else {"raw": str(data)[:100]}
    except ValueError:
        return {"raw": (arguments or "")[:100]}


def _summarize_result(data: dict) -> str:
    """工具成功结果的一句话摘要（事件流用；完整数据已回填模型）。"""
    if not isinstance(data, dict):
        return str(data)[:80]
    if "total_hits" in data:
        return f"命中 {data.get('total_hits')} 条，新候选 {data.get('new_candidates')}"
    if "added" in data:
        return f"入库 {data.get('added')} 篇"
    if "answer" in data:
        return str(data["answer"])[:60]
    if "total" in data:
        return f"库内共 {data.get('total')} 篇"
    if "suggestions" in data:
        return str(data["suggestions"])[:60]
    return json.dumps(data, ensure_ascii=False)[:80]


def _defaults_from_cfg(cfg: dict | None) -> tuple[int, int]:
    agent_cfg = (cfg or {}).get("agent") or {}
    return (
        int(agent_cfg.get("max_steps") or DEFAULT_MAX_STEPS),
        int(agent_cfg.get("token_budget") or DEFAULT_TOKEN_BUDGET),
    )


def run_agent_task(
    task: str,
    *,
    executor: ToolExecutor,
    cfg: dict | None = None,
    max_steps: int | None = None,
    token_budget: int | None = None,
    chat_fn: Callable[..., ChatResponse] | None = None,
    on_event: Callable[[AgentStep], None] | None = None,
    trace_path: Path | str | None = None,
) -> AgentRunResult:
    """执行一个 Agent 任务。chat_fn 可注入（测试用假模型）；默认走真实 API。

    max_steps / token_budget 缺省读 config.yaml 的 agent 节；trace_path 给出时
    把任务、每步工具调用、最终结果以 JSONL 逐步落盘。永不向上抛异常：
    LLM 故障转成 stopped_reason=error 的结果，由调用方渲染。
    """
    cfg_max_steps, cfg_budget = _defaults_from_cfg(cfg)
    max_steps = max_steps or cfg_max_steps
    token_budget = token_budget or cfg_budget

    trace = _TraceWriter(Path(trace_path), task, cfg) if trace_path else None
    if chat_fn is None:
        def chat_fn(messages, *, tools):
            return llm.chat_with_tools(messages, tools=tools, cfg=cfg)

    specs = executor.specs()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    steps: list[AgentStep] = []
    usage_total = {"prompt": 0, "completion": 0}
    guard = _RepeatGuard()

    def _accumulate(usage: dict) -> None:
        usage_total["prompt"] += usage.get("prompt", 0) or 0
        usage_total["completion"] += usage.get("completion", 0) or 0

    for step_no in range(1, max_steps + 1):
        used = usage_total["prompt"] + usage_total["completion"]
        if token_budget and used >= token_budget:
            break  # 预算闸：本轮开始前拦截，软着陆收尾
        try:
            resp = chat_fn(messages, tools=specs)
        except Exception as exc:
            result = AgentRunResult(
                answer=f"Agent 中途失败：{exc}", steps=steps, usage=usage_total, stopped_reason="error",
            )
            if trace:
                trace.final(result)
            return result
        _accumulate(resp.usage)

        if not resp.tool_calls:
            result = AgentRunResult(
                answer=(resp.content or "").strip(), steps=steps, usage=usage_total, stopped_reason="final",
            )
            if trace:
                trace.final(result)
            return result

        # 本轮声明了工具调用：assistant 消息（含 tool_calls）必须原样回进上下文，
        # 其后每个工具结果靠 tool_call_id 与调用一一配对——这是 function-calling 协议的核心
        messages.append(assistant_message(resp.content, resp.tool_calls))
        for tc in resp.tool_calls:
            nth = guard.check(_RepeatGuard.signature(tc.name, tc.arguments))
            tool_nth = guard.tool_nth(tc.name)
            duration_ms = 0
            retried = False
            if nth >= _RepeatGuard.BLOCK_AT:
                # 第 3 次完全相同的调用：拒绝执行（重复调用不会产生新结果）
                result = ToolResult(
                    tool=tc.name, status="fatal",
                    error=f"该调用与之前 {nth - 1} 次完全相同，已拒绝执行——请更换参数或直接给出最终回答",
                )
            else:
                t0 = time.perf_counter()
                result = executor.execute(tc.name, tc.arguments)
                if result.status == "retryable_error":
                    # 限流属暂时性失败：框架按 retry_after 自动重试一次（模型无感）
                    wait = min(result.retry_after or 15, MAX_AUTO_RETRY_WAIT)
                    time.sleep(wait)
                    result = executor.execute(tc.name, tc.arguments)
                    retried = True
                duration_ms = int((time.perf_counter() - t0) * 1000)
            step = AgentStep(
                step=step_no, tool=tc.name,
                arguments=_parse_args_for_display(tc.arguments),
                status=result.status,
                detail=(
                    ("（自动重试后）" if retried else "")
                    + (result.error or _summarize_result(result.data if isinstance(result.data, dict) else {}))
                ),
                duration_ms=duration_ms,
            )
            if nth == _RepeatGuard.WARN_AT:
                step.detail = f"（第 {nth} 次完全相同的调用）{step.detail}"
            steps.append(step)
            if trace:
                trace.step(step, retried)
            if on_event:
                on_event(step)
            payload = {"status": result.status, "data": result.data, "error": result.error}
            if nth == _RepeatGuard.WARN_AT:
                payload["warning"] = (
                    f"这是第 {nth} 次完全相同的调用。重复调用不会产生新结果——"
                    "请更换参数、换一个工具，或直接给出最终回答。"
                )
            elif tool_nth >= _RepeatGuard.WARN_TOOL_AT:
                payload["warning"] = guard.tool_warning(tc.name, tool_nth)
            messages.append(tool_message(tc.id, payload))

    # 步数/预算熔断：不允许再调工具，让模型基于已有信息收尾（软着陆，不硬报错）
    messages.append({"role": "user",
                     "content": "已达工具调用限制（步数或 token 预算）。请不要再调用工具，直接基于以上信息给出最终回答。"})
    try:
        resp = chat_fn(messages, tools=[])
        answer = (resp.content or "").strip()
    except Exception as exc:
        answer = f"已达工具调用限制（步数 {max_steps} / 预算 {token_budget}），最终总结生成失败：{exc}"
    used = usage_total["prompt"] + usage_total["completion"]
    reason = "token_budget" if token_budget and used >= token_budget else "max_steps"
    result = AgentRunResult(answer=answer, steps=steps, usage=usage_total, stopped_reason=reason)
    if trace:
        trace.final(result)
    return result
