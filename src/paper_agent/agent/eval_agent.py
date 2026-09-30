"""Agent 任务评测：任务完成率 / 工具使用合规 / 答案关键词命中 / 成本。

任务集见 eval/agent_tasks.json，kind 三类覆盖三种能力面：
- single_tool：单工具意图命中（模型选对工具）；
- multi_step：多步编排（检索→挑选入库、状态→检索→入库）；
- boundary：边界行为（域外拒绝不调工具、模糊指令澄清、空结果诚实汇报）。

指标（面试叙事的核心数字）：
- 任务完成率 completed：stopped_reason=final 且答案非空；
- 工具合规 tool_ok：must_call 全出现 且 forbidden 零出现（越界操作率 = 1 - 合规）；
- 答案判定 answer_ok：关键词组（组间 AND、组内 OR，与 RAG 评测同一逻辑）；
- 成本：平均步数、平均 tokens（从 AgentRunResult.usage 聚合）。

判定全部是纯函数（离线测试）；runner 只注入 executor 工厂与 chat_fn。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from paper_agent.agent.loop import AgentRunResult, AgentStep, run_agent_task
from paper_agent.agent.executor import ToolExecutor
from paper_agent.rag.eval import judge_answer

_TASK_KINDS = ("single_tool", "multi_step", "boundary")


@dataclass
class AgentTaskResult:
    """单条任务评测结果：轨迹 + 三项判定 + 违规明细。"""

    task_id: str
    kind: str
    task: str
    answer: str = ""
    stopped_reason: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    completed: bool = False
    tool_ok: bool | None = None
    answer_ok: bool | None = None
    violations: list[str] = field(default_factory=list)


def load_agent_tasks(path: Path) -> list[dict]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"任务集 {path} 缺少非空 cases 数组")
    for i, case in enumerate(cases):
        for key in ("id", "task", "kind"):
            if not case.get(key):
                raise ValueError(f"任务集第 {i} 条缺少 {key}")
        if case["kind"] not in _TASK_KINDS:
            raise ValueError(f"任务集第 {i} 条 kind 非法：{case['kind']}（{_TASK_KINDS}）")
    return cases


def judge_tool_usage(
    used: list[str], must_call: list[str] | None, forbidden: list[str] | None
) -> tuple[bool | None, list[str]]:
    """工具使用合规判定：must_call 全出现且 forbidden 零出现。

    两个约束都没给时返回 None（无法判定，如纯对话任务）。
    """
    if not must_call and not forbidden:
        return None, []
    violations: list[str] = []
    missing = [t for t in (must_call or []) if t not in used]
    if missing:
        violations.append(f"缺少期望工具调用：{', '.join(missing)}")
    # 去重后按 forbidden 语义逐名检查；越界调用是 agent 评测的硬红线
    unexpected = sorted({t for t in used if t in set(forbidden or [])})
    if unexpected:
        violations.append(f"出现禁止的工具调用：{', '.join(unexpected)}")
    return (not violations), violations


def run_agent_evalset(
    cases: list[dict],
    *,
    executor_factory: Callable[[], ToolExecutor],
    cfg: dict | None = None,
    chat_fn: Callable[..., AgentRunResult] | None = None,
    on_result: Callable[[AgentTaskResult], None] | None = None,
) -> list[AgentTaskResult]:
    """顺序执行全部任务。每个任务用独立 executor（预览状态互不污染）。

    on_result 在每个任务完成时回调（长跑时流式报进度，不必等全部结束）。
    """
    results: list[AgentTaskResult] = []
    for case in cases:
        executor = executor_factory()
        run = run_agent_task(
            case["task"],
            executor=executor,
            cfg=cfg,
            max_steps=case.get("max_steps"),
            chat_fn=chat_fn,
        )
        used = [s.tool for s in run.steps]
        tool_ok, violations = judge_tool_usage(used, case.get("must_call"), case.get("forbidden"))
        answer_ok = None
        if case.get("expect_keywords"):
            answer_ok = judge_answer({"expect_keywords": case["expect_keywords"]}, run.answer)
        results.append(AgentTaskResult(
            task_id=case["id"],
            kind=case["kind"],
            task=case["task"],
            answer=run.answer,
            stopped_reason=run.stopped_reason,
            steps=run.steps,
            tools_used=used,
            usage=run.usage,
            completed=run.stopped_reason == "final" and bool(run.answer.strip()),
            tool_ok=tool_ok,
            answer_ok=answer_ok,
            violations=violations,
        ))
        if on_result:
            on_result(results[-1])
    return results


def aggregate_results(results: list[AgentTaskResult]) -> dict:
    """聚合成一张汇报卡：完成率 / 工具合规率 / 答案命中率 / 成本 / 违规数。"""
    n = len(results) or 1
    judged_tool = [r for r in results if r.tool_ok is not None]
    judged_answer = [r for r in results if r.answer_ok is not None]
    tokens = lambda r: r.usage.get("prompt", 0) + r.usage.get("completion", 0)  # noqa: E731
    return {
        "total": len(results),
        "completion_rate": sum(r.completed for r in results) / n,
        "tool_ok_rate": (sum(r.tool_ok for r in judged_tool) / len(judged_tool)) if judged_tool else None,
        "answer_ok_rate": (sum(r.answer_ok for r in judged_answer) / len(judged_answer)) if judged_answer else None,
        "avg_steps": sum(len(r.steps) for r in results) / n,
        "avg_tokens": sum(tokens(r) for r in results) / n,
        "violations": sum(1 for r in results for _ in r.violations),
        "by_kind": {
            kind: {
                "total": sum(1 for r in results if r.kind == kind),
                "completed": sum(r.completed for r in results if r.kind == kind),
            }
            for kind in _TASK_KINDS
            if any(r.kind == kind for r in results)
        },
    }
