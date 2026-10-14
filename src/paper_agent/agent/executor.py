"""Agent 工具执行器：把 TOOL_REGISTRY 里的工具名绑定到本仓库的真实实现。

设计对应 PLAN §9 M5「统一工具契约」在 agent loop 中的落地：
- execute(name, arguments) 是唯一入口：解析 JSON 参数 → 分发 → run_tool 包装为
  四态 ToolResult，循环层只看 status 决定怎么把结果回填给模型；
- 会话状态：search_papers 的预览候选暂存在执行器实例里，add_papers 按
  序号（1,3-5）或 all 挑选入库——模型不需要在轮次之间搬运论文 JSON，
  只引用序号，避免大对象挤占上下文；
- 参数解析失败/未知工具一律 fatal（带可用工具清单）回填模型，让它自行纠正；
  数据源故障由 run_tool 映射为 degraded，模型可换参数重试或如实告知用户。

M19 复现执行器：write_code/run_code 给模型一双「手」——代码落在
data/repro/<id>/workspace 隔离工作区，run_code 以独立子进程执行（超时/
输出截断/文件名白名单），执行前过 HITL 确认门（危险操作类工具复用
M15 的 confirm 插座）。运行崩溃不算工具失败：ok 结果里带 stderr，
模型读报错自行修代码——这正是 agent 范式的自纠错闭环。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from paper_agent import paths
from paper_agent.config import load_config
from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.rag.qa import Retriever, answer_question, sources_from_hits
from paper_agent.services.assistant import suggest_directions
from paper_agent.services.facts import load_bound_fields
from paper_agent.services.search import Candidate, ingest_papers, search_sources
from paper_agent.tools import TOOL_REGISTRY, ToolResult, run_tool

_MAX_CANDIDATES_SHOWN = 30  # 单次检索回填给模型的最大候选数（上下文保护）
_MAX_CODE_BYTES = 30 * 1024  # 单文件代码上限

# 锚定任务的工具作用域（M26）：这些工具会改库或把注意力引向其它论文，
# 是 M25 漂移事故的直接肇因——锚定任务下从工具面移除并拒绝执行。
ANCHOR_DENIED_TOOLS = frozenset({"search_papers", "add_papers", "explore_directions"})
_MAX_EVIDENCE_CHARS = 3200  # 保留完整知识块；280 字会截掉正文里的数据集与指标
_DEFAULT_COMPARE = 4  # 未指定论文时的默认对比篇数


def _evidence_excerpt(text: str, budget: int) -> str:
    lo, hi = 0, min(len(text), _MAX_EVIDENCE_CHARS)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(json.dumps(text[:mid], ensure_ascii=False)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def parse_pick_spec(spec: str, total: int) -> list[int]:
    """解析序号表达式（"1,3-5"）为 1-based 序号列表；越界/非法项跳过。"""
    picked: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, _, end_s = part.partition("-")
            if not (start_s.strip().isdigit() and end_s.strip().isdigit()):
                continue
            lo, hi = int(start_s), int(end_s)
        elif part.isdigit():
            lo = hi = int(part)
        else:
            continue
        if lo > hi:
            lo, hi = hi, lo
        picked.extend(n for n in range(lo, hi + 1) if 1 <= n <= total)
    return sorted(set(picked))


def _int_or_none(value: Any, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 需要整数，收到 {value!r}") from exc


class ToolExecutor:
    """一次 Agent 任务的工具执行环境（持有库连接、检索器与本轮预览状态）。

    confirm：HITL 人工确认钩子（M15）。签名 confirm(count, titles) -> bool；
    为 None 时自动放行（评测/非交互场景）。add_papers 单次达到
    confirm_threshold 篇即触发——未来接入删除/下载/执行类工具时复用同一插座。
    """

    def __init__(
        self,
        *,
        lib: Library,
        retriever: Retriever | None = None,
        cfg: dict | None = None,
        params: dict | None = None,
        confirm: Callable[[int, list[str]], bool] | None = None,
        anchor: str | None = None,
    ):
        self._lib = lib
        self._retriever = retriever
        self._cfg = cfg or load_config()
        self._params = dict(params or {})
        self._preview: list[Candidate] = []  # 最近一次 search_papers 的候选
        agent_cfg = (self._cfg.get("agent") or {}) if isinstance(self._cfg, dict) else {}
        self._add_cap = int(agent_cfg.get("add_cap", 20))
        self._confirm_threshold = int(agent_cfg.get("confirm_threshold", 5))
        self._confirm = confirm
        # M26 任务锚定：锚定复现任务时移除易致漂移的工具（最小权限工具集）。
        # 漂移事故（M25）：续任务未锚定 source_id，模型自行检索、入库了另一篇
        # 论文并在其工作区写代码——prompt 约束只是建议，作用域在这里代码强制。
        self._anchor = (anchor or "").strip() or None
        self._denied = ANCHOR_DENIED_TOOLS if self._anchor else frozenset()

    @staticmethod
    def specs() -> list[dict]:
        """TOOL_REGISTRY → OpenAI tools 参数格式（type/function 包一层）。

        M26 起锚定任务下移除作用域外工具（见 instance 版 specs_for）。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta["description"],
                    "parameters": meta["inputSchema"],
                },
            }
            for name, meta in TOOL_REGISTRY.items()
        ]

    def specs_for(self) -> list[dict]:
        """当前执行器可见的工具规格：锚定任务隐藏作用域外工具——模型看不到
        就不会调用，比调用后再拒绝更省一步。execute 侧仍保留拦截（防御纵深）。"""
        if not self._denied:
            return self.specs()
        return [s for s in self.specs() if s["function"]["name"] not in self._denied]

    @property
    def supported_tools(self) -> list[str]:
        return sorted(TOOL_REGISTRY)

    def execute(self, name: str, arguments: str) -> ToolResult:
        """循环层唯一入口：工具名 + 原始 JSON 参数 → 四态 ToolResult。"""
        if name not in TOOL_REGISTRY:
            return ToolResult(
                tool=name, status="fatal",
                error=f"未知工具：{name}（可用：{', '.join(self.supported_tools)}）",
            )
        if name in self._denied:
            return ToolResult(
                tool=name, status="fatal",
                error=(
                    f"本任务已锚定论文 {self._anchor}，{name} 超出任务作用域（防任务漂移）；"
                    "请专注完成锚定任务本身"
                ),
            )
        try:
            args = json.loads(arguments) if arguments and arguments.strip() else {}
        except ValueError as exc:
            return ToolResult(tool=name, status="fatal", error=f"参数不是合法 JSON：{exc}")
        if not isinstance(args, dict):
            return ToolResult(tool=name, status="fatal", error=f"参数必须是 JSON 对象，收到 {type(args).__name__}")
        handlers: dict[str, Callable[[dict], Any]] = {
            "search_papers": self._tool_search,
            "add_papers": self._tool_add,
            "ask_library": self._tool_ask,
            "library_status": self._tool_status,
            "explore_directions": self._tool_explore,
            "compare_papers": self._tool_compare,
            "read_repro_plan": self._tool_read_plan,
            "write_code": self._tool_write_code,
            "run_code": self._tool_run_code,
        }
        return run_tool(name, lambda: handlers[name](args))

    # ------------------------------------------------------------------
    # 各工具实现（返回 JSON 可序列化的 dict；异常交给 run_tool 分类）
    # ------------------------------------------------------------------

    def _tool_search(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        preview = search_sources(
            query,
            lib=self._lib,
            cfg=self._cfg,
            sources=str(args.get("sources") or "openalex,europepmc,arxiv"),
            max_results=_int_or_none(args.get("max_results"), "max_results") or 10,
            year_from=_int_or_none(args.get("year_from"), "year_from"),
            year_to=_int_or_none(args.get("year_to"), "year_to"),
            raw=True,  # 检索词由模型按英文学术表述直接给出，不再过查询优化器
            field=str(args.get("field") or "all"),
            author=str(args["author"]) if args.get("author") else None,
            sort=str(args.get("sort") or "relevance"),
            oa_only=bool(args.get("oa_only")),
        )
        self._preview = preview.candidates
        items = [
            {
                "i": i,
                "id": c.paper.source_id,
                "title": c.paper.title,
                "year": c.paper.year,
                "citations": c.paper.citations,
                "fulltext": bool(c.paper.pdf_urls),
                "in_library": bool(c.duplicate_of),
            }
            for i, c in enumerate(preview.candidates, start=1)
        ]
        new_count = sum(1 for c in preview.candidates if not c.duplicate_of)
        out = {
            "query": query,
            "total_hits": preview.total_hits,
            "shown": len(items),
            "new_candidates": new_count,
            "candidates": items[:_MAX_CANDIDATES_SHOWN],
            "notes": preview.notes[:10],
            "degraded_sources": sorted(preview.degraded_sources),
            "hint": "如需入库请调用 add_papers（pick=序号，如 \"1,3-5\"；缺省收全部新候选）",
        }
        # M15 教训（eval search-add-01）：全源故障时 0 结果 ≠ 没有论文。
        # 不把「源挂了」伪装成正常空结果，模型才能做出「稍后重试/如实汇报」的正确决策
        if preview.total_hits == 0 and preview.degraded_sources:
            out["warning"] = (
                f"本次 0 结果大概率是数据源故障（{', '.join(sorted(preview.degraded_sources))}）"
                "而非没有相关论文：不要立即换词重搜，可如实告知用户稍后重试。"
            )
        return out

    def _tool_add(self, args: dict) -> dict:
        if not self._preview:
            raise ValueError("没有可入库的预览结果：先调用 search_papers")
        pick = str(args.get("pick") or "all").strip() or "all"
        if pick.lower() == "all":
            papers = [c.paper for c in self._preview if not c.duplicate_of]
        else:
            picked = parse_pick_spec(pick, len(self._preview))
            if not picked:
                raise ValueError(f"pick 表达式「{pick}」没有匹配到候选（共 {len(self._preview)} 条）")
            papers = [self._preview[n - 1].paper for n in picked]
        if not papers:
            return {"added": 0, "note": "没有新的候选可入库（全部已在库中）"}
        if len(papers) > self._add_cap:
            # 数量上限是硬闸：直接拒绝并教模型分批，而不是静默截断
            raise ValueError(
                f"单次入库 {len(papers)} 篇超过上限 {self._add_cap}：请用 pick 表达式分批挑选最重要的候选"
            )
        if self._confirm and len(papers) >= self._confirm_threshold:
            titles = [p.title[:50] for p in papers]
            if not self._confirm(len(papers), titles):
                return {"added": 0, "declined": True,
                        "note": "用户拒绝了本次入库：不要重复尝试同样的入库操作，可在回答中说明"}
        result = ingest_papers(papers, lib=self._lib, tag=str(args["tag"]) if args.get("tag") else None)
        return {
            "added": len(result.new_papers),
            "duplicates_merged": result.duplicates,
            "titles": [r.paper.title[:60] for r in result.new_papers],
        }

    def _tool_ask(self, args: dict) -> dict:
        question = str(args.get("question") or "").strip()
        if not question:
            raise ValueError("question 不能为空")
        if self._retriever is None or self._retriever.store.count() == 0:
            return {"answer": "向量库为空，无法回答库内问题（可先 search_papers 检索相关论文）。",
                    "sources": [], "empty_index": True}
        try:
            answer, hits = answer_question(question, retriever=self._retriever)
        except RuntimeError as exc:  # 缺 Key / 限流退避无效等配置或可用性问题
            return {"answer": f"问答链暂时不可用：{exc}", "sources": [], "error": True}
        return {"answer": answer, "sources": sources_from_hits(hits)}

    def _tool_status(self, _args: dict) -> dict:
        counts = self._lib.counts()
        return {"total": sum(counts.values()), "by_status": counts}

    def _tool_explore(self, _args: dict) -> dict:
        return {"suggestions": suggest_directions(self._lib)}

    # ------------------------------------------------------------------
    # 跨论文对比（M19）：护城河工具——证据来自本库向量索引，普通 AI 没有这份数据
    # ------------------------------------------------------------------

    def _tool_compare(self, args: dict) -> dict:
        topic = str(args.get("topic") or "").strip()
        if not topic:
            raise ValueError("topic 不能为空")
        if self._retriever is None or self._retriever.store.count() == 0:
            return {"topic": topic, "papers": [], "note": "向量库为空：先检索并处理论文建立索引"}

        ids = [str(x).strip() for x in (args.get("paper_ids") or []) if str(x).strip()]
        if not ids:
            # 缺省取最近入库的已处理论文（analyzed/indexed 才有知识块）
            processed = [
                r for r in self._lib.list() if r.status in ("analyzed", "indexed")
            ]
            ids = [r.paper.source_id for r in processed[:_DEFAULT_COMPARE]]
        if not ids:
            return {"topic": topic, "papers": [], "note": "库里还没有已处理的论文"}

        k = max(1, min(int(args.get("k_per_paper") or 3), 5))
        papers_out: list[dict] = []
        missing: list[str] = []
        for sid in ids[:6]:  # 单次对比上限 6 篇（上下文保护）
            rec = self._lib.get(sid)
            if rec is None and ":" not in sid:
                # 模型常把 openalex:W... 简写成 W...；只接受完整后缀的唯一匹配。
                matches = [pid for pid in self._lib.find(sid) if pid.split(":", 1)[-1] == sid]
                if len(matches) > 1:
                    raise ValueError(f"论文 ID {sid} 匹配多篇，请使用完整 source_id：{', '.join(matches)}")
                if len(matches) == 1:
                    sid = matches[0]
                    rec = self._lib.get(sid)
            if rec is None:
                missing.append(sid)
                continue
            hits = self._retriever.retrieve(topic, k=k, paper_id=sid)
            structured = {}
            experiment_path = paths.knowledge_dir(self._cfg) / safe_dirname(sid) / "experiment.json"
            if experiment_path.exists():
                try:
                    experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
                    structured = {key: experiment.get(key) for key in ("research_goal", "datasets", "metrics")}
                except (ValueError, OSError, AttributeError):
                    pass
            papers_out.append({
                "paper_id": sid,
                "title": rec.paper.title[:80],
                "extracted_facts": structured if len(json.dumps(structured, ensure_ascii=False)) <= 2000 else {},
                "field_evidence": load_bound_fields(
                    sid, self._cfg,
                    self._retriever.store.all_hits(paper_id=sid)
                    if callable(getattr(self._retriever.store, "all_hits", None)) else hits,
                    max_chars=6000,
                ),
                "evidence": [
                    {"section": h.section, "page": h.page,
                     "text": _evidence_excerpt(h.text, 32_000 // (max(1, len(ids[:6])) * k)),
                     "chunk_id": getattr(h, "chunk_id", "")}
                    for h in hits
                ],
            })
        return {
            "topic": topic,
            "papers": papers_out,
            "missing": missing,
            "hint": "逐字段查看 field_evidence：引用有效不等于事实得到支持；unverified 是待核对候选，unknown/missing 不可补写，unsupported/conflict 不可当作确定事实。extracted_facts 仅为辅助产物。只依据原文综合对比，逐项标注来源；任务、数据划分、指标定义或单位未核对时，禁止按裸值排序论文优劣。",
        }

    # ------------------------------------------------------------------
    # 复现执行器（M19）：read_repro_plan → write_code → run_code 自纠错闭环
    # ------------------------------------------------------------------

    def _workspace(self, source_id: str) -> Path:
        sid = str(source_id or "").strip()
        if not sid:
            raise ValueError("source_id 不能为空")
        if self._lib.get(sid) is None:
            raise ValueError(f"未找到论文：{sid}（source_id 需为库内 id）")
        ws = paths.data_dir(self._cfg) / "repro" / safe_dirname(sid) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    @staticmethod
    def _code_file(workspace: Path, filename: str) -> Path:
        name = str(filename or "").strip()
        if not name or Path(name).name != name:
            raise ValueError(f"文件名非法（只允许纯文件名）：{filename!r}")
        if not name.endswith(".py"):
            raise ValueError("只允许执行/写入 .py 文件")
        return workspace / name

    @staticmethod
    def _record_run(workspace: Path, payload: dict) -> None:
        """把一次真实执行追加进 runs.jsonl（工作区旁）：UI 与审计共用的运行档案。

        只记录真正跑过的执行；被确认门拒绝的调用没有发生，不产生记录。
        """
        record = {"ts": time.time(), **payload}
        runs_path = workspace.parent / "runs.jsonl"
        with runs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _tool_read_plan(self, args: dict) -> dict:
        sid = str(args.get("source_id") or "").strip()
        if not sid:
            raise ValueError("source_id 不能为空")
        rec = self._lib.get(sid)
        if rec is None:
            raise ValueError(f"未找到论文：{sid}")
        knowledge = paths.knowledge_dir(self._cfg) / safe_dirname(sid)
        plan_path = knowledge / "repro_plan.md"
        exp_path = knowledge / "experiment.json"
        if not plan_path.exists():
            raise ValueError(
                f"论文 {sid} 还没有复现方案：请先在详情页「生成复现方案」（依赖已完成的实验结构）"
            )
        plan = plan_path.read_text(encoding="utf-8")
        experiment = None
        if exp_path.exists():
            try:
                experiment = json.loads(exp_path.read_text(encoding="utf-8"))
            except ValueError:
                experiment = None
        return {
            "source_id": sid,
            "title": rec.paper.title,
            "repro_plan": plan[:3500] + ("…（已截断）" if len(plan) > 3500 else ""),
            "experiment": experiment,
            "hint": "基于方案写最小可运行原型（write_code），再 run_code 执行验证",
        }

    def _tool_write_code(self, args: dict) -> dict:
        workspace = self._workspace(args.get("source_id"))
        target = self._code_file(workspace, str(args.get("filename") or ""))
        content = str(args.get("content") or "")
        if not content.strip():
            raise ValueError("content 不能为空（要写入完整代码）")
        if len(content.encode("utf-8")) > _MAX_CODE_BYTES:
            raise ValueError(f"代码超过 {_MAX_CODE_BYTES // 1024}KB 上限：请拆分成更小的模块")
        target.write_text(content, encoding="utf-8")
        return {
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "hint": "已写入。调用 run_code 执行（执行前用户会收到确认请求）",
        }

    def _tool_run_code(self, args: dict) -> dict:
        workspace = self._workspace(args.get("source_id"))
        target = self._code_file(workspace, str(args.get("filename") or ""))
        if not target.exists():
            raise ValueError(f"文件不存在：{target.name}（先用 write_code 写入）")
        try:
            timeout = int(args.get("timeout_sec") or 60)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"timeout_sec 需要整数：{args.get('timeout_sec')!r}") from exc
        timeout = max(5, min(timeout, 180))

        # 代码执行属危险操作类：无条件过 HITL 确认门（M15 插座复用）
        if self._confirm and not self._confirm(1, [f"运行代码 {target.name}（工作区 {workspace.name}）"]):
            return {
                "ran": False, "declined": True,
                "note": "用户拒绝了本次代码执行：不要重复尝试运行，可在回答中说明方案并等待用户指示",
            }

        started = time.monotonic()
        try:
            proc = subprocess.run(
                # -u 无缓冲：超时杀进程时已打印的内容仍能从管道读到——
                # M25 教训：块缓冲下超时 = 空 stdout，诊断无从下手
                [sys.executable, "-u", target.name],
                cwd=workspace,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or b""
            err = exc.stderr or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            self._record_run(workspace, {
                "filename": target.name, "exit_code": None, "timed_out": True,
                "timeout_sec": timeout,
                "stdout": out[:3000], "stderr": (err or f"进程超过 {timeout}s 被终止")[:3000],
            })
            return {
                "ran": True, "timed_out": True, "exit_code": None,
                "timeout_sec": timeout,
                "stdout": out[:3000], "stderr": (err or f"进程超过 {timeout}s 被终止")[:3000],
                "note": "超时：优化算法复杂度、缩小数据规模或加大 timeout_sec 后重试",
            }
        duration = int((time.monotonic() - started) * 1000)
        stdout = proc.stdout.decode("utf-8", errors="replace")[:3000]
        stderr = proc.stderr.decode("utf-8", errors="replace")[:3000]
        self._record_run(workspace, {
            "filename": target.name, "exit_code": proc.returncode, "timed_out": False,
            "duration_ms": duration, "stdout": stdout, "stderr": stderr,
        })
        return {
            "ran": True,
            "exit_code": proc.returncode,
            "duration_ms": duration,
            "stdout": stdout,
            "stderr": stderr,
            "note": "exit_code 非 0 时读 stderr 修代码重跑；成功则汇报输出中的关键指标",
        }
