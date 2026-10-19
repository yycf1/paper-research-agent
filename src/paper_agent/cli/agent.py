"""Agent 模式命令：pa agent "任务"——LLM 自主调用工具完成多步任务。

护栏在 loop/executor 层强制（步数熔断 / token 预算 / 循环检测 / 入库上限），
本层只透传参数与渲染；HITL 确认钩子在这里接到交互终端。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import typer
from rich.table import Table

from paper_agent import paths
from paper_agent.agent import ToolExecutor, run_agent_task
from paper_agent.cli.core import FAIL, _data_dir, _library, app, console
from paper_agent.config import PROJECT_ROOT, load_config
from paper_agent.rag.qa import Retriever
from paper_agent.rag.vectorstore import VectorStore

_STATUS_MARK = {
    "ok": "[green]ok[/green]",
    "degraded": "[yellow]degraded[/yellow]",
    "retryable_error": "[yellow]retryable[/yellow]",
    "fatal": "[red]fatal[/red]",
}

_STOP_REASON_MARK = {
    "max_steps": "[yellow]⚠ 已达步数上限，以下为基于现有信息的总结[/yellow]\n",
    "token_budget": "[yellow]⚠ 已达 token 预算，以下为基于现有信息的总结[/yellow]\n",
    "error": "[red]⚠ Agent 中途异常[/red]\n",
}


def _interactive_confirm(count: int, titles: list[str]) -> bool:
    """HITL 确认回调：达到阈值的入库操作先展示再放行。"""
    console.print(f"[yellow]⚠ 即将入库 {count} 篇论文：[/yellow]")
    for title in titles[:5]:
        console.print(f"  [dim]-[/dim] {title}")
    if len(titles) > 5:
        console.print(f"  …等共 {len(titles)} 篇")
    return typer.confirm("确认入库？", default=False)


@app.command()
def agent(
    task: str = typer.Argument(..., help="任务描述（自然语言，如「找 2 篇 2024 年的 GNN 推荐综述入库」）"),
    max_steps: int = typer.Option(0, "--max-steps", min=0, max=50, help="工具调用步数上限（0 = 读 config.yaml）"),
    token_budget: int = typer.Option(0, "--token-budget", min=0, help="token 预算（0 = 读 config.yaml）"),
    trace: bool = typer.Option(False, "--trace", help="把步骤轨迹持久化为 JSONL（data/traces/ 下）"),
    anchor: str = typer.Option("", "--anchor", help="锚定论文 id：约束本任务只针对该论文工作区（检索/入库类工具被移除，防续任务漂移）"),
    paper: list[str] | None = typer.Option(None, "--paper", help="限定任务论文（可重复，最多 6 篇）"),
) -> None:
    """Agent 模式：模型自主决定调哪些工具、调多少轮（区别于 pa chat 的固定路由）。"""
    cfg = load_config()
    lib = _library(cfg)
    try:
        anchor_id = (anchor or "").strip() or None
        if anchor_id:
            from paper_agent.server.routes import _resolve_sid  # 复用同一套 id 片段解析

            anchor_id = _resolve_sid(anchor_id, lib)
            console.print(f"[yellow]⚓ 任务锚定：{anchor_id}[/yellow]")
        store = VectorStore(_data_dir(cfg) / "db")
        retriever = Retriever(store) if store.count() > 0 else None
        try:
            executor = ToolExecutor(lib=lib, retriever=retriever, cfg=cfg, confirm=_interactive_confirm,
                                    anchor=anchor_id, allowed_paper_ids=paper)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

        trace_path = None
        if trace:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            trace_path = paths.data_dir(cfg) / "traces" / f"agent-{stamp}.jsonl"

        shown_steps = max_steps or cfg.get("agent", {}).get("max_steps", 8)
        console.print(f"[bold]Agent 任务[/bold]（最多 {shown_steps} 步工具调用）：{task}\n")

        def _on_event(step) -> None:
            args_text = json.dumps(step.arguments, ensure_ascii=False)
            if len(args_text) > 72:
                args_text = args_text[:72] + "…"
            console.print(
                f"  [dim]step {step.step}[/dim] → [cyan]{step.tool}[/cyan]({args_text}) "
                f"{_STATUS_MARK.get(step.status, step.status)} "
                f"[dim]{step.duration_ms} ms[/dim]"
                + (f"｜{step.detail}" if step.detail else "")
            )

        result = run_agent_task(
            task, executor=executor, cfg=cfg,
            max_steps=max_steps or None, token_budget=token_budget or None,
            on_event=_on_event, trace_path=trace_path, anchor=anchor_id,
        )
        console.print()
        console.print(_STOP_REASON_MARK.get(result.stopped_reason, ""))
        console.print(f"[bold]答[/bold] {result.answer or '（模型未返回内容）'}")
        tokens = result.usage.get("prompt", 0) + result.usage.get("completion", 0)
        console.print(f"\n[dim]工具调用 {len(result.steps)} 步 · 约 {tokens} tokens[/dim]")
        if trace_path:
            console.print(f"[dim]轨迹已存档：{Path(trace_path)}[/dim]")
        if result.stopped_reason == "error":
            raise typer.Exit(code=1)
    finally:
        lib.close()


@app.command("eval-agent")
def eval_agent_cmd(
    tasks: Path = typer.Option(
        PROJECT_ROOT / "eval" / "agent_tasks.json", "--tasks", help="任务集 JSON 路径"
    ),
    trace: bool = typer.Option(False, "--trace", help="每个任务的轨迹存档到 data/traces/agent-eval/"),
) -> None:
    """Agent 任务评测：结果验收 / 工具合规 / 关键词命中 / 已报告用量（消耗 LLM 调用）。"""
    from paper_agent.agent.eval_agent import (
        aggregate_results,
        load_agent_tasks,
        run_agent_evalset,
    )

    cfg = load_config()
    try:
        cases = load_agent_tasks(tasks)
    except Exception as exc:
        console.print(f"{FAIL} 任务集加载失败：{exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[bold]Agent 任务评测[/bold]：{len(cases)} 个任务\n")

    lib = _library(cfg)
    try:
        store = VectorStore(_data_dir(cfg) / "db")
        retriever = Retriever(store, cfg=cfg) if store.count() > 0 else None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trace_dir = paths.data_dir(cfg) / "traces" / "agent-eval" / stamp if trace else None

        def _factory() -> ToolExecutor:
            return ToolExecutor(lib=lib, retriever=retriever, cfg=cfg)

        def _on_result(r) -> None:
            mark = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "–"}
            console.print(
                f"  [dim]{r.task_id}[/dim] {mark[r.completed]}{mark[r.tool_ok]}{mark[r.answer_ok]} "
                f"[dim]{len(r.steps)} 步｜工具：{','.join(r.tools_used) or '无'}[/dim]"
            )

        results = run_agent_evalset(cases, executor_factory=_factory, cfg=cfg,
                                    on_result=_on_result, trace_dir=trace_dir)
    finally:
        lib.close()

    table = Table(show_header=True, header_style="bold")
    table.add_column("case", style="cyan", no_wrap=True)
    table.add_column("kind", no_wrap=True)
    table.add_column("步数", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("工具", overflow="fold", max_width=30)
    table.add_column("结果验收", justify="center")
    table.add_column("合规", justify="center")
    table.add_column("答案", justify="center")
    for r in results:
        mark = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "–"}
        tokens = r.usage.get("prompt", 0) + r.usage.get("completion", 0)
        table.add_row(
            r.task_id, r.kind, str(len(r.steps)), str(tokens),
            ",".join(r.tools_used) or "–",
            {"passed": "通过", "failed": "未达成", "partial": "部分完成", "unverified": "待核验"}.get(r.verification.get('status'), '待核验'),
            mark[r.tool_ok], mark[r.answer_ok],
        )
        if trace_dir:
            trace_dir.mkdir(parents=True, exist_ok=True)
            out = trace_dir / f"result-{results.index(r):03d}.json"
            out.write_text(json.dumps({
                "task": r.task, "answer": r.answer, "stopped_reason": r.stopped_reason,
                "usage": r.usage, "violations": r.violations,
                "verification": r.verification, "telemetry": r.telemetry,
                "execution_finished": r.execution_finished, "completed": r.completed,
                "steps": [dict(step=idx + 1, tool=s.tool, arguments=s.arguments,
                               status=s.status, detail=s.detail)
                          for idx, s in enumerate(r.steps)],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(table)

    agg = aggregate_results(results)
    console.print(
        f"\n[bold]已核验完成占全部任务 {agg['completion_rate']:.0%}[/bold]"
        + f"｜执行结束 {agg['execution_finished_rate']:.0%}｜待核验 {agg['unverified_tasks']} 项"
        + (f"｜工具合规率 {agg['tool_ok_rate']:.0%}" if agg["tool_ok_rate"] is not None else "")
        + (f"｜答案命中率 {agg['answer_ok_rate']:.0%}" if agg["answer_ok_rate"] is not None else "")
        + f"｜平均 {agg['avg_steps']:.1f} 步 / {agg['avg_tokens']:.0f} tokens"
        + f"｜工具约束问题 {agg['violations']} 项"
        + f"｜用量完整 {agg['usage_complete_tasks']}/{agg['total']} 项"
    )
    for kind, stat in agg["by_kind"].items():
        console.print(f"  [dim]{kind}：{stat['completed']}/{stat['total']} 完成[/dim]")
    failed = [r for r in results if r.violations]
    for r in failed:
        console.print(f"[red]✗ {r.task_id}[/red]：{'；'.join(r.violations)}")
