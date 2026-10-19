"""Agent 后台任务：步骤事件实时可见 + HITL 确认门的进程内状态。

与通用 jobs.py 的差别：agent 循环边跑边产生步骤事件（工具调用轨迹），
轮询方需要增量读取；确认钩子要把「暂停等用户决定」做成可轮询的状态——
用 threading.Event 实现：confirm 回调把任务置为 waiting_confirm 并阻塞
任务线程，前端轮询看到 pending_confirm 弹确认窗，POST /confirm 置
Event 放行或拒绝。

超时兜底：等待确认超过 CONFIRM_TIMEOUT 秒按「拒绝」处理（宁可少做不可
多做），防止用户关页导致任务线程永久挂起。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from paper_agent import paths
from paper_agent.agent import ToolExecutor, run_agent_task
from paper_agent.rag.qa import Retriever
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.observability import current_summary

CONFIRM_TIMEOUT = 300  # 秒；超时按拒绝处理（宁可少做不可多做）


@dataclass
class AgentTaskState:
    """一次 Agent 任务的完整状态（轮询端点直接序列化返回）。"""

    id: str
    task: str
    status: str = "running"  # running | waiting_confirm | done | error
    steps: list[dict] = field(default_factory=list)
    pending_confirm: dict | None = None  # {"count": n, "titles": [...]}
    answer: str = ""
    stopped_reason: str = ""
    usage: dict = field(default_factory=dict)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    anchor: str = ""  # M26：锚定论文 id（空 = 未锚定）
    allowed_paper_ids: list[str] | None = None
    acceptance: list[dict] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    telemetry: dict = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self, *, with_steps: bool = True) -> dict:
        out = {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "pending_confirm": self.pending_confirm,
            "answer": self.answer,
            "stopped_reason": self.stopped_reason,
            "usage": self.usage,
            "error": self.error,
            "step_count": len(self.steps),
            "anchor": self.anchor,
            "allowed_paper_ids": self.allowed_paper_ids,
            "verification": self.verification,
            "telemetry": self.telemetry,
            "duration_ms": self.duration_ms,
        }
        if with_steps:
            out["steps"] = self.steps
        return out


@dataclass
class _Handle:
    """任务句柄：状态 + 确认事件 + 拒绝标记。"""

    state: AgentTaskState
    event: threading.Event = field(default_factory=threading.Event)
    declined: list[bool] = field(default_factory=list)  # 非空即「用户拒绝了」


class _Registry:
    """任务表。锁只保护表结构；字段读写在 GIL 下安全，任务体在各自线程跑。"""

    def __init__(self) -> None:
        self._tasks: dict[str, _Handle] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        task: str,
        *,
        cfg: dict,
        chat_fn: Callable | None = None,
        max_steps: int | None = None,
        token_budget: int | None = None,
        trace_path: Path | None = None,
        task_id: str | None = None,
        anchor: str | None = None,
        allowed_paper_ids: list[str] | None = None,
        acceptance: list[dict] | None = None,
        history: list[tuple[str, str]] | None = None,
    ) -> AgentTaskState:
        """提交任务并在守护线程执行。chat_fn 可注入（测试用假模型）。

        task_id：调用方预生成并与轨迹文件名共享（M23），任务表条目与磁盘
        轨迹用同一 id 关联——服务重启后历史条目可由轨迹回读。
        anchor（M26）：锚定论文 id，注入循环层系统提示 + 收紧执行器工具
        作用域，防续任务漂移（M25 事故）。
        """
        state = AgentTaskState(
            id=task_id or uuid.uuid4().hex[:12], task=task,
            started_at=time.time(), anchor=(anchor or "").strip(),
            allowed_paper_ids=list(allowed_paper_ids) if allowed_paper_ids is not None else ([anchor] if anchor else None),
            acceptance=acceptance or [],
        )
        handle = _Handle(state=state)
        with self._lock:
            self._tasks[state.id] = handle
        threading.Thread(
            target=self._run,
            args=(handle, cfg, chat_fn, max_steps, token_budget, trace_path, state.anchor, history),
            daemon=True,
        ).start()
        return state

    def list_tasks(self) -> list[AgentTaskState]:
        """全部任务（新→旧）。注册表在内存中：重启后由轨迹文件补齐历史。"""
        with self._lock:
            return sorted(
                (h.state for h in self._tasks.values()),
                key=lambda s: s.started_at,
                reverse=True,
            )

    def decide(self, task_id: str, approve: bool) -> bool:
        """用户对确认门的决断：置位 Event 唤醒任务线程。返回是否有待决任务。"""
        handle = self._tasks.get(task_id)
        if handle is None or handle.state.status != "waiting_confirm":
            return False
        if not approve:
            handle.declined.append(True)
        handle.event.set()
        return True

    def get(self, task_id: str) -> AgentTaskState | None:
        with self._lock:
            handle = self._tasks.get(task_id)
            return handle.state if handle else None

    # ------------------------------------------------------------------

    def _run(
        self,
        handle: _Handle,
        cfg: dict,
        chat_fn: Callable | None,
        max_steps: int | None,
        token_budget: int | None,
        trace_path: Path | None,
        anchor: str = "",
        history: list[tuple[str, str]] | None = None,
    ) -> None:
        state = handle.state

        def _confirm(count: int, titles: list[str]) -> bool:
            state.pending_confirm = {"count": count, "titles": titles}
            state.status = "waiting_confirm"
            approved = handle.event.wait(timeout=CONFIRM_TIMEOUT)
            state.pending_confirm = None
            state.status = "running"
            return bool(approved) and not handle.declined

        def _on_event(step) -> None:
            state.steps.append(asdict(step))

        def _on_call(_call) -> None:
            state.telemetry = current_summary()
            state.usage = {k: state.telemetry.get(k, 0) for k in ('prompt', 'completion')}

        try:
            job_lib = paths.library(cfg)  # 任务线程用自己的账本连接
            try:
                store = VectorStore(paths.vector_dir(cfg))
                retriever = Retriever(store, cfg=cfg) if store.count() > 0 else None
                executor = ToolExecutor(
                    lib=job_lib, retriever=retriever, cfg=cfg, confirm=_confirm,
                    anchor=anchor or None,
                    allowed_paper_ids=state.allowed_paper_ids,
                )
                result = run_agent_task(
                    state.task,
                    history=history,
                    executor=executor,
                    cfg=cfg,
                    max_steps=max_steps,
                    token_budget=token_budget,
                    chat_fn=chat_fn,
                    on_event=_on_event,
                    trace_path=trace_path,
                    anchor=anchor or None,
                    acceptance=state.acceptance,
                    on_call=_on_call,
                )
                state.answer = result.answer
                state.stopped_reason = result.stopped_reason
                state.usage = result.usage
                state.verification = result.verification
                state.telemetry = result.telemetry
                state.duration_ms = result.duration_ms
                if result.stopped_reason == "error":
                    state.status = "error"
                    state.error = result.answer
                else:
                    state.status = "done"
            finally:
                job_lib.close()
        except Exception as exc:  # 意外错误：转状态不崩溃
            state.status = "error"
            state.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            state.pending_confirm = None
            state.finished_at = time.time()


registry = _Registry()
