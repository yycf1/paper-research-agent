"""统一工具契约：四态 ToolResult + 结果合理性校验 + 工具注册表（MCP 风格 inputSchema）。

设计约定（PLAN.md M5）：
- 所有工具统一返回 ToolResult；调用方按 status 决定重试/降级/报错，不感知底层异常类型；
- 结果进入下游（LLM 上下文/入库）前先过 validate_*，垃圾数据在边界拦截；
- TOOL_REGISTRY 按 MCP 规范描述每个工具（description + inputSchema），为二期 MCP server 打地基。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from paper_agent.models import Paper
from paper_agent.sources import SourceUnavailable
from paper_agent.sources import arxiv as arxiv_src
from paper_agent.sources import europepmc as epmc_src
from paper_agent.sources import openalex as oa_src

ToolStatus = Literal["ok", "retryable_error", "degraded", "fatal"]

# 限流特征：数据源错误消息里的状态码/措辞（各源统一为 "HTTP 429: …"）
_RATE_LIMIT_RE = re.compile(r"429|rate.?limit|too many requests", re.IGNORECASE)

SOURCE_MODULES = {"openalex": oa_src, "europepmc": epmc_src, "arxiv": arxiv_src}


@dataclass
class ToolResult:
    """一次工具调用的统一结果。status 语义：
    - ok：成功，data 可直接使用；
    - retryable_error：暂时性失败（限流/超时），可按 retry_after 重试；
    - degraded：该工具本次不可用（如数据源被屏蔽），跳过不阻塞全局；
    - fatal：参数/用法错误，重试无意义。
    """

    tool: str
    status: ToolStatus
    data: Any = None
    error: str = ""
    retry_after: float | None = None
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def run_tool(
    name: str,
    fn: Callable[[], Any],
    *,
    fatal_errors: tuple[type[BaseException], ...] = (ValueError,),
) -> ToolResult:
    """执行工具函数并包装为 ToolResult。

    异常 → 状态映射：fatal_errors → fatal；SourceUnavailable → degraded
    （HTTP 层已做过一次自动重试，到达这里的源失败按「本次不可用」处理）；
    其余意外异常 → degraded 并保留原始信息（不吞栈）。
    """
    try:
        return ToolResult(tool=name, status="ok", data=fn())
    except fatal_errors as exc:
        return ToolResult(tool=name, status="fatal", error=f"参数或用法错误：{exc}")
    except SourceUnavailable as exc:
        # M15：retryable_error 语义落地——限流（429 等）属暂时性失败，框架层
        # 可按 retry_after 自动重试；其余源故障仍按 degraded 交由上层换路。
        if _RATE_LIMIT_RE.search(str(exc)):
            return ToolResult(tool=name, status="retryable_error", error=str(exc), retry_after=15)
        return ToolResult(tool=name, status="degraded", error=str(exc))
    except Exception as exc:  # 意外异常：按不可用处理但标记 unexpected 供排查
        return ToolResult(
            tool=name, status="degraded", error=f"[unexpected] {type(exc).__name__}: {exc}",
            meta={"unexpected": True},
        )


# ---------------------------------------------------------------------------
# 检索工具：数据源 client 的契约化封装
# ---------------------------------------------------------------------------


def search_source_tool(
    source_name: str,
    *,
    query: str,
    max_results: int,
    year_from: int | None = None,
    proxy: str = "",
    email: str = "",
    field: str = "all",
    author: str | None = None,
    sort: str = "relevance",
    year_to: int | None = None,
    oa_only: bool = False,
) -> ToolResult:
    """在单个数据源上检索论文。degraded = 该源本次不可用（跳过，不阻塞其它源）。

    高级检索条件（M13）经统一契约透传：field（all/title/abstract）、author、
    sort（relevance/citations/newest）、year_to、oa_only；各源按自身能力映射，
    不支持的条件由服务层后过滤兜底（如 arXiv 无引用数据）。
    """
    if source_name not in SOURCE_MODULES:
        return ToolResult(tool="search_papers", status="fatal",
                          error=f"未知数据源：{source_name}")
    if max_results < 1 or max_results > 50:
        return ToolResult(tool="search_papers", status="fatal",
                          error=f"max_results 越界：{max_results}（1~50）")

    def _run() -> list[Paper]:
        module = SOURCE_MODULES[source_name]
        if source_name == "openalex":
            return module.search(
                query, max_results, year_from, proxy=proxy, email=email,
                field=field, author=author, sort=sort, year_to=year_to,
            )
        return module.search(
            query, max_results, year_from, proxy=proxy,
            field=field, author=author, sort=sort, year_to=year_to, oa_only=oa_only,
        )

    result = run_tool("search_papers", _run)
    result.meta["source"] = source_name
    return result


# ---------------------------------------------------------------------------
# 结果合理性校验：垃圾数据在进下游（入库/LLM）前拦截
# ---------------------------------------------------------------------------


def validate_papers(papers: list[Paper], *, current_year: int = 2026) -> list[str]:
    """校验检索结果结构合理性，返回问题列表（空列表 = 通过）。

    每条 issue 以 source_id 开头，便于 filter_valid_papers 按条目过滤。
    """
    issues: list[str] = []
    for p in papers:
        if not p.title.strip():
            issues.append(f"{p.source_id}: 标题为空")
        if not p.source_id or ":" not in p.source_id:
            issues.append(f"{p.source_id!r}: source_id 非法")
        if p.year is not None and not (1900 <= p.year <= current_year + 1):
            issues.append(f"{p.source_id}: 年份越界 {p.year}")
    return issues


def filter_valid_papers(papers: list[Paper], *, current_year: int = 2026) -> tuple[list[Paper], list[str]]:
    """过滤掉结构非法的论文，返回 (合法列表, 问题列表)。入库前的最后一道闸。"""
    clean: list[Paper] = []
    issues: list[str] = []
    for p in papers:
        iss = validate_papers([p], current_year=current_year)
        if iss:
            issues.extend(iss)
        else:
            clean.append(p)
    return clean, issues


# ---------------------------------------------------------------------------
# 工具注册表：MCP 风格描述（二期 MCP server 直接消费）
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "search_papers": {
        "description": (
            "按主题检索学术论文（OpenAlex/Europe PMC/arXiv 多源）。只检索预览不写库；"
            "query 请用英文学术表述。结果带序号，供 add_papers 按序号挑选入库"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索主题词（建议英文学术表述）"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50,
                                "description": "每源返回条数上限"},
                "year_from": {"type": "integer", "description": "只保留该年份及以后"},
                "year_to": {"type": "integer", "description": "只保留该年份及以前"},
                "field": {"type": "string", "enum": ["all", "title", "abstract"],
                          "description": "关键词匹配范围：全部 / 仅标题 / 仅摘要"},
                "author": {"type": "string", "description": "按作者过滤（模糊匹配）"},
                "sort": {"type": "string", "enum": ["relevance", "citations", "newest"],
                         "description": "排序：相关度 / 引用数 / 最新"},
                "oa_only": {"type": "boolean", "description": "只保留有全文可下载的结果"},
                "sources": {"type": "string",
                            "description": "逗号分隔的数据源（openalex,europepmc,arxiv），缺省全用"},
            },
            "required": ["query"],
        },
    },
    "add_papers": {
        "description": "把最近一次 search_papers 预览中的候选论文入库（写文献库账本）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pick": {
                    "type": "string",
                    "description": (
                        "要入库的候选序号，如 \"1,3-5\"；缺省或 \"all\" 收入全部"
                        "「库内没有」的新候选"
                    ),
                },
                "tag": {"type": "string", "description": "可选：入库时归入该分组"},
            },
        },
    },
    "ask_library": {
        "description": "就已入库论文内容问答，回答带出处引用；库外问题如实回答「未提及」",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "paper_id": {"type": "string", "description": "可选：只查询该论文，与 paper_ids 互斥"},
                "paper_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6,
                              "description": "可选：查询论文子集；不能超出宿主指定的任务范围"},
            },
            "required": ["question"],
        },
    },
    "library_status": {
        "description": "查看文献库各处理阶段的论文数量与状态",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "explore_directions": {
        "description": "用户不清楚方向时，基于库内论文主题分布建议可探索的研究方向",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "compare_papers": {
        "description": (
            "跨论文对比：按主题在库内多篇论文各自检索最相关的知识块，返回分组证据"
            "（带章节/页码）。综合对比表/差异分析前必须先用它收集证据，不要凭记忆对比"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "对比主题（用于在各论文内检索证据）"},
                "paper_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                    "description": "要对比的论文 id 列表（库内 source_id）；缺省取最近已入库的 4 篇",
                },
                "k_per_paper": {"type": "integer", "minimum": 1, "maximum": 5,
                                "description": "每篇论文检索的证据块数（缺省 3）"},
            },
            "required": ["topic"],
        },
    },
    "read_repro_plan": {
        "description": "读取一篇论文的复现方案（repro_plan.md）与实验结构（experiment.json），供写复现代码前理解方法与步骤",
        "inputSchema": {
            "type": "object",
            "properties": {"source_id": {"type": "string", "description": "库内论文 id"}},
            "required": ["source_id"],
        },
    },
    "write_code": {
        "description": "把复现/原型代码写入该论文的隔离工作区（data/repro/<id>/workspace）。只写 .py 文件，文件名不能带路径",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "库内论文 id（决定工作区）"},
                "filename": {"type": "string", "description": "代码文件名（仅文件名，如 baseline.py）"},
                "content": {"type": "string", "description": "完整代码内容（UTF-8 源码）"},
            },
            "required": ["source_id", "filename", "content"],
        },
    },
    "run_code": {
        "description": (
            "在隔离工作区以独立进程执行已写入的 .py 文件（默认超时 60s，上限 180s），"
            "返回退出码/stdout/stderr。执行前需用户确认；stderr 有报错时修代码重跑，"
            "运行成功则汇报输出与指标"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "库内论文 id（决定工作区）"},
                "filename": {"type": "string", "description": "要执行的 .py 文件名"},
                "timeout_sec": {"type": "integer", "minimum": 5, "maximum": 180,
                                "description": "超时秒数（缺省 60）"},
            },
            "required": ["source_id", "filename"],
        },
    },
}
