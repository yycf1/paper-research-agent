"""检索编排服务：查询优化 → 多源检索 → 结果校验 → 跨源去重 →（可选）入库。

M10 起拆成两层，Web 端支持「先预览再挑选入库」：
- search_sources：只检索与校验，对照账本标记重复，不写库；
- ingest_papers：把选中的候选入库，可附分组标签（tag）；
- search_and_ingest：两层组合（全自动模式），CLI/对话助手沿用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from paper_agent.graph.route import optimize_query
from paper_agent.library import Library, PaperRecord
from paper_agent.models import Paper, normalize_title
from paper_agent.tools import SOURCE_MODULES, filter_valid_papers, search_source_tool


@dataclass
class Candidate:
    """一条检索候选。duplicate_of 非空表示库内已有（含同 DOI/同题）。"""

    paper: Paper
    duplicate_of: str | None = None


@dataclass
class PreviewResult:
    """检索预览（未写库）。"""

    candidates: list[Candidate] = field(default_factory=list)
    total_hits: int = 0
    notes: list[str] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    source_hits: dict[str, int] = field(default_factory=dict)  # 源名 → 候选数（跨子查询累计）


@dataclass
class SearchOutcome:
    """全自动检索入库的结果（search_sources + ingest_papers 的组合输出）。"""

    new_papers: list[PaperRecord] = field(default_factory=list)
    total_hits: int = 0
    notes: list[str] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    source_hits: dict[str, int] = field(default_factory=dict)


@dataclass
class IngestResult:
    new_papers: list[PaperRecord] = field(default_factory=list)
    duplicates: int = 0


def search_sources(
    query: str,
    *,
    lib: Library,
    cfg: dict,
    sources: str | list[str] = "openalex,europepmc,arxiv",
    max_results: int = 10,
    year_from: int | None = None,
    raw: bool = False,
    field: str = "all",
    author: str | None = None,
    sort: str = "relevance",
    min_citations: int | None = None,
    oa_only: bool = False,
    year_to: int | None = None,
) -> PreviewResult:
    """执行多源检索与校验，对照账本标记重复；不写库。未知数据源抛 ValueError。

    高级检索条件（M13）：field/author/sort/year_to 透传各源；min_citations 与
    oa_only 为跨源统一语义的后过滤（引用数未知按 0 计、全文以 pdf_urls 非空为准），
    不依赖单源能力，保证「同一条件在所有源上含义一致」。
    """
    if field not in ("all", "title", "abstract"):
        raise ValueError(f"非法匹配范围：{field}（all/title/abstract）")
    if sort not in ("relevance", "citations", "newest"):
        raise ValueError(f"非法排序：{sort}（relevance/citations/newest）")
    if min_citations is not None and min_citations < 0:
        raise ValueError("最低引用数不能为负")
    if year_from and year_to and year_from > year_to:
        raise ValueError(f"年份区间为空：{year_from} > {year_to}")
    if isinstance(sources, str):
        names = [s.strip().lower() for s in sources.split(",")]
    else:
        names = [s.strip().lower() for s in sources]
    names = [n for n in names if n]
    unknown = [n for n in names if n not in SOURCE_MODULES]
    if unknown:
        raise ValueError(f"未知数据源：{', '.join(unknown)}（可用：{', '.join(SOURCE_MODULES)}）")

    proxy = cfg.get("proxy", "") or ""
    source_cfg = cfg.get("sources", {}) or {}

    if raw:
        plan = {"topics": [query], "year_from": year_from, "max_results": None, "optimized": False}
    else:
        plan = optimize_query(query)
    eff_year = year_from or plan.get("year_from")
    eff_max = plan.get("max_results") or max_results

    result = PreviewResult(plan=plan)
    active_filters: list[str] = []
    if author:
        active_filters.append(f"作者 {author}")
    if field != "all":
        active_filters.append({"title": "仅标题", "abstract": "仅摘要"}[field])
    if min_citations:
        active_filters.append(f"最低被引 {min_citations}")
    if oa_only:
        active_filters.append("仅可下载全文")
    if year_to:
        active_filters.append(f"年份 ≤ {year_to}")
    if active_filters:
        result.notes.append("过滤条件：" + " · ".join(active_filters))
    trimmed = 0

    seen: dict[str, str] = {}  # 去重键（doi 或归一化标题）→ 本次检索内的代表 id
    rendered: set[str] = set()  # 已渲染候选的代表 id（库内已有条目按其账本 id 计）

    def _post_filter(papers: list[Paper]) -> list[Paper]:
        """跨源统一后过滤：最低引用数 + 仅全文。返回过滤后的列表。"""
        nonlocal trimmed
        if min_citations is None and not oa_only:
            return papers
        kept = [
            p for p in papers
            if (min_citations is None or (p.citations or 0) >= min_citations)
            and (not oa_only or bool(p.pdf_urls))
        ]
        trimmed += len(papers) - len(kept)
        return kept

    def _search_one(keyword: str) -> int:
        """对单个查询词跑全部源并就地去重，返回各源返回的候选总数。"""
        hits = 0
        for name in names:
            if not (source_cfg.get(name) or {}).get("enabled", True):
                result.notes.append(f"{name}：已在 config.yaml 停用，跳过")
                continue
            email = (source_cfg.get(name) or {}).get("email", "") or ""
            tool_result = search_source_tool(
                name, query=keyword, max_results=eff_max, year_from=eff_year,
                proxy=proxy, email=email,
                field=field, author=author, sort=sort,
                year_to=year_to, oa_only=oa_only,
            )
            if tool_result.status == "fatal":
                result.notes.append(f"{name}：{tool_result.error}")
                continue
            if tool_result.status != "ok":
                hint = "可在 config.yaml 配置 proxy" if name == "arxiv" else "请检查网络"
                result.notes.append(f"{name} 不可用（{tool_result.error}），已跳过——{hint}")
                continue
            papers, issues = filter_valid_papers(tool_result.data)
            result.notes.extend(f"结果校验拦截：{issue}" for issue in issues)
            papers = _post_filter(papers)
            hits += len(papers)
            result.source_hits[name] = result.source_hits.get(name, 0) + len(papers)
            for p in papers:
                dup_id = _duplicate_of(lib, seen, p)
                rep = dup_id or p.source_id
                if rep in rendered:
                    # 同一论文（同 DOI/同题/同 sid）不重复渲染：否则预览里
                    # 一次出现为「新候选」、一次出现为「库内已有」，互相矛盾
                    continue
                rendered.add(rep)
                result.candidates.append(Candidate(paper=p, duplicate_of=dup_id))
                if p.doi:
                    seen[f"doi:{p.doi}"] = rep
                seen[f"t:{normalize_title(p.title)}"] = rep
        return hits

    for topic in plan["topics"]:
        result.total_hits += _search_one(topic)

    # 优化后的检索词全军覆没 → 回退原始词重搜一轮
    if result.total_hits == 0 and plan.get("optimized"):
        result.notes.append("优化后的检索词没有结果，回退原始词重搜")
        result.total_hits = _search_one(query)
    if trimmed:
        result.notes.append(f"已按过滤条件剔除 {trimmed} 条不符合的结果")
    return result


def ingest_papers(
    papers: list[Paper],
    *,
    lib: Library,
    tag: str | None = None,
) -> IngestResult:
    """把选中的候选入库；再次对照账本去重（预览与入库之间库可能已变化）。"""
    out = IngestResult()
    for p in papers:
        is_new = lib.upsert_paper(p, tag=tag)
        if is_new:
            rec = lib.get(p.source_id)
            if rec:
                out.new_papers.append(rec)
        else:
            out.duplicates += 1
    return out


def search_and_ingest(
    query: str,
    *,
    lib: Library,
    cfg: dict,
    sources: str | list[str] = "openalex,europepmc,arxiv",
    max_results: int = 10,
    year_from: int | None = None,
    raw: bool = False,
    tag: str | None = None,
    field: str = "all",
    author: str | None = None,
    sort: str = "relevance",
    min_citations: int | None = None,
    oa_only: bool = False,
    year_to: int | None = None,
) -> SearchOutcome:
    """全自动模式：检索全部候选并全部入库（CLI `pa search` / 对话助手用）。"""
    preview = search_sources(
        query, lib=lib, cfg=cfg, sources=sources,
        max_results=max_results, year_from=year_from, raw=raw,
        field=field, author=author, sort=sort,
        min_citations=min_citations, oa_only=oa_only, year_to=year_to,
    )
    ingest = ingest_papers(
        [c.paper for c in preview.candidates],  # 重复项也传：upsert 幂等合并元数据
        lib=lib, tag=tag or query,
    )
    return SearchOutcome(
        new_papers=ingest.new_papers,
        total_hits=preview.total_hits,
        notes=preview.notes,
        plan=preview.plan,
        source_hits=preview.source_hits,
    )


def _duplicate_of(lib: Library, seen: dict[str, str], p: Paper) -> str | None:
    """先用本次会话的内存索引去重，再查账本（跨命令重复检索也不重复入库）。"""
    if p.doi and seen.get(f"doi:{p.doi}"):
        return seen[f"doi:{p.doi}"]
    tkey = f"t:{normalize_title(p.title)}"
    if seen.get(tkey):
        return seen[tkey]
    return lib.find_duplicate(p)
