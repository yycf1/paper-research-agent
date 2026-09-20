"""文献生命周期命令：search / download / add / parse / analyze / index。"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from paper_agent.cli.core import (
    FAIL,
    OK,
    WARN,
    _data_dir,
    _knowledge_dir,
    _library,
    _parsed_dir,
    _papers_dir,
    _print_papers,
    _resolve,
    app,
    console,
)
from paper_agent.config import load_config
from paper_agent.download import DownloadError, download_pdf, safe_dirname
from paper_agent.extraction.experiment_flow import extract_experiment
from paper_agent.extraction.report import EmptyReportError, generate_report
from paper_agent.llm import chat
from paper_agent.parsing import extract_markdown
from paper_agent.rag.embedder import embed
from paper_agent.rag.splitter import chunk_abstract, split_markdown
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.services.search import ingest_papers, search_sources


@app.command()
def search(
    query: str = typer.Argument(..., help="检索关键词（支持中英文，自动优化为英文检索词）"),
    source: str = typer.Option(
        "openalex,europepmc,arxiv", "--source", "-s", help="逗号分隔的数据源"
    ),
    max: int = typer.Option(10, "--max", "-m", min=1, help="每源返回条数上限"),
    year_from: int | None = typer.Option(None, "--year-from", help="只保留该年份及以后"),
    year_to: int | None = typer.Option(None, "--year-to", help="只保留该年份及以前"),
    field: str = typer.Option(
        "all", "--in", help="关键词匹配范围：all / title / abstract"
    ),
    author: str = typer.Option("", "--author", help="按作者过滤（模糊匹配）"),
    sort_key: str = typer.Option(
        "relevance", "--sort", help="排序：relevance / citations / newest"
    ),
    min_citations: int | None = typer.Option(None, "--min-citations", min=0, help="最低被引数"),
    oa_only: bool = typer.Option(False, "--oa", help="只保留有全文可下载的结果"),
    raw: bool = typer.Option(False, "--raw", help="跳过查询优化，原样直搜"),
    ingest: bool = typer.Option(False, "--ingest", help="全部候选直接入库（旧的全自动行为）"),
    pick: str = typer.Option(
        "", "--pick", help="按表格行号入库指定候选，如 --pick 1,3-5（预览表中的 # 列）"
    ),
    tag: str = typer.Option("", "--tag", help="入库分组标签（缺省用检索词）"),
) -> None:
    """检索论文：默认预览不写库（机器找，人挑选）；--ingest 全收，--pick 选收。

    默认先用 LLM 优化查询（中译英、缩写展开、复合需求拆子查询），失败自动降级原样直搜。
    高级条件（--in/--author/--sort/--min-citations/--oa/--year-to）作用于全部数据源。
    """
    cfg = load_config()
    lib = _library(cfg)
    try:
        try:
            preview = search_sources(
                query, lib=lib, cfg=cfg, sources=source,
                max_results=max, year_from=year_from, raw=raw,
                field=field, author=author or None, sort=sort_key,
                min_citations=min_citations, oa_only=oa_only, year_to=year_to,
            )
        except ValueError as exc:
            console.print(f"{FAIL} {exc}")
            raise typer.Exit(code=1) from exc

        plan = preview.plan
        if plan.get("optimized"):
            console.print(
                f"[dim]查询优化：{' / '.join(plan['topics'])}"
                + (f"（{plan['year_from']} 年起）" if plan.get("year_from") else "")
                + "[/dim]"
            )
        for note in preview.notes:
            console.print(f"{WARN} {note}")
        for name, hits in preview.source_hits.items():
            console.print(f"[cyan]{name}[/cyan]：{hits} 条候选")

        label = " / ".join(plan["topics"]) if plan.get("optimized") else query
        fresh = sum(1 for c in preview.candidates if not c.duplicate_of)
        _print_candidates(
            preview.candidates,
            title=f"检索「{label}」：{preview.total_hits} 条候选（新 {fresh}，库内已有 {len(preview.candidates) - fresh}）",
        )

        chosen: list = []
        if pick:
            chosen = _pick_candidates(preview.candidates, pick)
        elif ingest:
            chosen = [c.paper for c in preview.candidates]

        if not chosen:
            console.print(
                "\n预览模式不写库。入库方式：[cyan]--pick 1,3-5[/cyan] 选收、"
                "[cyan]--ingest[/cyan] 全收；或到 Web 界面勾选入库。"
            )
            if preview.total_hits == 0:
                console.print(
                    "没有检索到结果。可以尝试：放宽或去掉 [cyan]--year-from[/cyan]、"
                    "换更通用的关键词、或加 [cyan]--raw[/cyan] 原样直搜。"
                )
            return

        result = ingest_papers(chosen, lib=lib, tag=tag or query)
        if result.new_papers:
            _print_papers(
                result.new_papers,
                title=f"已入库 {len(result.new_papers)} 篇（{result.duplicates} 篇与库内已有重复，仅合并信息）",
            )
            console.print(
                "\n下一步：[cyan]pa download --all[/cyan] 下载 PDF，"
                "或 [cyan]pa process <id>[/cyan] 单篇一键处理。"
            )
        else:
            console.print(f"{OK} 候选均已存在于库中（{result.duplicates} 篇重复，仅合并信息）。")
    finally:
        lib.close()


def _print_candidates(candidates, *, title: str) -> None:
    """检索预览表：带行号（供 --pick），库内已有的候选标灰。"""
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("源", no_wrap=True)
    table.add_column("年份", justify="right")
    table.add_column("引用", justify="right")
    table.add_column("PDF", justify="center")
    table.add_column("标题", overflow="fold", max_width=52)
    for i, cand in enumerate(candidates, start=1):
        p = cand.paper
        pdf_mark = "[green]✓[/green]" if p.pdf_urls else "[yellow]仅摘要[/yellow]"
        dup = f"[dim]{p.title}（库内已有）[/dim]" if cand.duplicate_of else p.title
        table.add_row(
            str(i),
            p.source_id.split(":", 1)[1],
            p.source,
            str(p.year or "-"),
            str(p.citations if p.citations is not None else "-"),
            pdf_mark,
            dup,
        )
    console.print(table)


def _pick_candidates(candidates, spec: str) -> list:
    """解析行号表达式（1,3-5）为候选 Paper 列表；越界行号警告跳过。"""
    chosen: list = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            if not (start_s.strip().isdigit() and end_s.strip().isdigit()):
                console.print(f"{WARN} 无法解析行号「{part}」，跳过")
                continue
            lo, hi = int(start_s), int(end_s)
        elif part.isdigit():
            lo = hi = int(part)
        else:
            console.print(f"{WARN} 无法解析行号「{part}」，跳过")
            continue
        if lo > hi:
            lo, hi = hi, lo
        for n in range(lo, hi + 1):
            if 1 <= n <= len(candidates):
                chosen.append(candidates[n - 1].paper)
            else:
                console.print(f"{WARN} 行号 {n} 超出范围（1~{len(candidates)}），跳过")
    # 同一候选可能被区间重叠选中两次；按 source_id 保序去重
    return list({p.source_id: p for p in reversed(chosen)}.values())[::-1]


@app.command()
def download(
    ids: list[str] = typer.Argument(None, help="论文 id（可用 source_id 片段，如 W274... 或 2401.12345）"),
    all: bool = typer.Option(False, "--all", help="下载所有 discovered 状态的论文"),
    limit: int = typer.Option(10, "--limit", "-l", min=1, help="--all 模式的下载上限"),
) -> None:
    """下载论文 PDF（开放获取）；失败标记 download_failed，不中断其余下载。"""
    cfg = load_config()
    proxy = cfg.get("proxy", "") or ""
    lib = _library(cfg)
    try:
        if all:
            targets = [r for r in lib.list(status="discovered") if r.paper.pdf_urls]
            skipped_no_pdf = sum(1 for r in lib.list(status="discovered") if not r.paper.pdf_urls)
            targets = targets[:limit]
            if skipped_no_pdf:
                console.print(f"{WARN} {skipped_no_pdf} 篇无 OA 全文（仅摘要），自动跳过")
        else:
            targets = _resolve(lib, ids or [])
        if not targets:
            console.print("没有待下载的论文。先用 [cyan]pa search[/cyan] 检索入库。")
            return

        ok = fail = 0
        for rec in targets:
            p = rec.paper
            try:
                path = download_pdf(p, _papers_dir(cfg), proxy=proxy)
                lib.set_status(p.source_id, "downloaded", error=None, pdf_path=str(path))
                size_kb = path.stat().st_size / 1024
                console.print(f"{OK} [dim]{p.source_id}[/dim] {p.title[:40]}…（{size_kb:.0f} KB）")
                ok += 1
            except DownloadError as exc:
                lib.set_status(p.source_id, "download_failed", error=str(exc)[:500])
                console.print(f"{FAIL} [dim]{p.source_id}[/dim] {p.title[:40]}…：{str(exc)[:120]}")
                fail += 1
        console.print(f"\n下载完成：{ok} 成功，{fail} 失败（失败项可用同样命令重试）")
    finally:
        lib.close()


@app.command()
def add(
    paths: list[Path] = typer.Argument(..., exists=True, readable=True, help="本地 PDF/DOCX 文件路径"),
) -> None:
    """导入本地论文（中文文献、已有 PDF 的入口），状态直达 downloaded。

    同一文件重复导入按内容哈希判重，不会产生重复条目。
    """
    from paper_agent.services.library_ops import UnsupportedFormat, import_local_file

    cfg = load_config()
    lib = _library(cfg)
    try:
        for path in paths:
            try:
                rec, is_new = import_local_file(Path(path), cfg=cfg, lib=lib)
            except UnsupportedFormat as exc:
                console.print(f"{FAIL} {Path(path).name}：{exc}")
                continue
            mark = "新导入" if is_new else "已存在（幂等合并）"
            console.print(f"{OK} [dim]{rec.paper.source_id}[/dim] {rec.paper.title}——{mark}，状态 {rec.status}")
    finally:
        lib.close()


@app.command()
def tag(
    ids: list[str] = typer.Argument(..., help="论文 id 片段（可多个）"),
    set_name: str = typer.Option("", "--set", help="把论文归入该分组（组不存在则自动创建）"),
    clear: bool = typer.Option(False, "--clear", help="移出分组（回到未分组）"),
) -> None:
    """主动归类：把论文移入/移出分组（Web 界面的分组管理与此等价）。"""
    if not set_name and not clear:
        console.print(f"{FAIL} 需要 --set <分组名> 或 --clear（二选一）")
        raise typer.Exit(code=1) from None
    if set_name and clear:
        console.print(f"{FAIL} --set 与 --clear 只能二选一")
        raise typer.Exit(code=1) from None
    cfg = load_config()
    lib = _library(cfg)
    try:
        targets = _resolve(lib, ids)
        if not targets:
            console.print("没有匹配的论文。")
            return
        new_tag = None if clear else set_name.strip()
        ok = 0
        for rec in targets:
            sid = rec.paper.source_id
            lib.set_tag(sid, new_tag)
            mark = f"→ 分组「{new_tag}」" if new_tag else "→ 已移出分组"
            console.print(f"{OK} [dim]{sid}[/dim] {rec.paper.title[:36]}… {mark}")
            ok += 1
        console.print(f"\n归类完成：{ok} 篇。")
    finally:
        lib.close()


@app.command()
def parse(
    ids: list[str] = typer.Argument(None, help="论文 id 片段；缺省配合 --all"),
    all: bool = typer.Option(False, "--all", help="解析所有 downloaded 状态论文"),
) -> None:
    """PDF/DOCX → 分节 Markdown（缓存于 data/parsed/{id}/full_text.md）。"""
    cfg = load_config()
    lib = _library(cfg)
    try:
        targets = _resolve(lib, ids or []) if ids else [r for r in lib.list(status="downloaded")]
        if not all and not ids:
            targets = [r for r in lib.list(status="downloaded")]
        if not targets:
            console.print("没有待解析的论文（downloaded 状态）。")
            return
        ok = fail = 0
        for rec in targets:
            sid, p = rec.paper.source_id, rec.paper
            if not rec.pdf_path or not Path(rec.pdf_path).exists():
                console.print(f"{FAIL} [dim]{sid}[/dim] {p.title[:36]}…：原文文件缺失")
                fail += 1
                continue
            try:
                md = extract_markdown(rec.pdf_path)
            except Exception as exc:
                lib.set_status(sid, "downloaded", error=f"解析失败：{exc}"[:500])
                console.print(f"{FAIL} [dim]{sid}[/dim] {p.title[:36]}…：解析失败 {exc}")
                fail += 1
                continue
            # 空结果守卫仅针对 PDF（扫描版常见）；DOCX 无 OCR 问题，短文档也放行
            min_chars = 30 if rec.pdf_path.lower().endswith(".docx") else 200
            if len(md.strip()) < min_chars:
                lib.set_status(sid, "downloaded", error="解析结果近乎为空（可能是扫描版 PDF）")
                console.print(f"{FAIL} [dim]{sid}[/dim] {p.title[:36]}…：解析结果近乎为空")
                fail += 1
                continue
            out = _parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            sections = md.count("\n## ")
            lib.set_status(sid, "parsed", error=None)
            console.print(f"{OK} [dim]{sid}[/dim] {p.title[:36]}…（{len(md)} 字符，{sections} 节）")
            ok += 1
        console.print(f"\n解析完成：{ok} 成功，{fail} 失败")
    finally:
        lib.close()


@app.command()
def analyze(
    ids: list[str] = typer.Argument(None, help="论文 id 片段；缺省配合 --all"),
    all: bool = typer.Option(False, "--all", help="分析所有 parsed 状态论文"),
) -> None:
    """LLM 知识提取：生成 experiment.json + report.md（状态 analyzed）。

    需要 LLM API Key（.env）。仅 downloaded 未解析的论文会自动先解析。
    --all 额外扫描 analyzed/indexed 中报告缺失或为空的论文（M11 前空回复
    会写空报告），只重新生成报告、不重复提取实验知识。
    """
    cfg = load_config()
    lib = _library(cfg)
    try:
        repair_ids: set[str] = set()
        if ids:
            targets = _resolve(lib, ids)
        elif all:
            targets = lib.list(status="parsed")
            for rec in lib.list(status="analyzed") + lib.list(status="indexed"):
                rp = _knowledge_dir(cfg) / safe_dirname(rec.paper.source_id) / "report.md"
                if not rp.exists() or len(rp.read_text(encoding="utf-8").strip()) < 30:
                    targets.append(rec)
                    repair_ids.add(rec.paper.source_id)
        else:
            targets = []
        if not targets:
            console.print("没有待分析的论文（parsed 状态）。")
            return
        ok = fail = repaired = 0
        total_tokens = 0
        for rec in targets:
            sid, p = rec.paper.source_id, rec.paper
            parsed_path = _parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
            if not parsed_path.exists():
                console.print(f"{WARN} [dim]{sid}[/dim] 未解析，自动补跑 parse…")
                if rec.status != "downloaded" or not rec.pdf_path:
                    console.print(f"{FAIL} [dim]{sid}[/dim] 无法解析（无原文），跳过")
                    fail += 1
                    continue
                try:
                    md = extract_markdown(rec.pdf_path)
                    parsed_path.parent.mkdir(parents=True, exist_ok=True)
                    parsed_path.write_text(md, encoding="utf-8")
                    lib.set_status(sid, "parsed")
                except Exception as exc:
                    console.print(f"{FAIL} [dim]{sid}[/dim] 自动解析失败：{exc}")
                    fail += 1
                    continue
            out_dir = _knowledge_dir(cfg) / safe_dirname(sid)
            try:
                markdown = parsed_path.read_text(encoding="utf-8")
                experiment = None
                if sid in repair_ids:
                    exp_path = out_dir / "experiment.json"
                    if exp_path.exists():
                        try:
                            loaded = json.loads(exp_path.read_text(encoding="utf-8"))
                            if isinstance(loaded, dict) and loaded.get("token_usage"):
                                experiment = loaded
                        except ValueError:
                            experiment = None
                if experiment is not None:
                    console.print(f"⏳ [dim]{sid}[/dim] {p.title[:36]}… 报告缺失，仅重生成精读报告…")
                    report, report_usage = generate_report(markdown, chat_fn=chat)
                    tokens = report_usage["prompt"] + report_usage["completion"]
                    repaired += 1
                else:
                    console.print(f"⏳ [dim]{sid}[/dim] {p.title[:36]}… 提取实验知识…")
                    experiment = extract_experiment(markdown, chat_fn=chat)
                    console.print(f"⏳ [dim]{sid}[/dim] {p.title[:36]}… 生成精读报告…")
                    report, report_usage = generate_report(markdown, chat_fn=chat)
                    tokens = (
                        experiment["token_usage"]["prompt"] + experiment["token_usage"]["completion"]
                        + report_usage["prompt"] + report_usage["completion"]
                    )
                total_tokens += tokens
            except EmptyReportError as exc:  # 单篇失败：状态保持 parsed，不中断其余论文
                lib.set_status(sid, "parsed", error=f"分析失败：{exc}"[:500])
                console.print(f"{FAIL} [dim]{sid}[/dim] {p.title[:36]}…：{exc}")
                fail += 1
                continue
            except RuntimeError as exc:  # 缺 Key 等配置问题，直接中断
                console.print(f"{FAIL} {exc}")
                raise typer.Exit(code=1) from exc
            except Exception as exc:
                lib.set_status(sid, "parsed", error=f"分析失败：{exc}"[:500])
                console.print(f"{FAIL} [dim]{sid}[/dim] {p.title[:36]}…：{str(exc)[:160]}")
                fail += 1
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "experiment.json").write_text(
                json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (out_dir / "report.md").write_text(report, encoding="utf-8")
            # 修复目标保留原状态（indexed 不降级，否则重试扫描又看不见）
            lib.set_status(sid, rec.status if sid in repair_ids else "analyzed", error=None)
            low_conf = sum(1 for v in (experiment.get("confidence") or {}).values() if v == "low")
            console.print(
                f"{OK} [dim]{sid}[/dim] {p.title[:36]}…（~{tokens} tokens"
                + (f"，{low_conf} 个低置信字段" if low_conf else "")
                + "）"
            )
            ok += 1
        extra = f"，其中 {repaired} 篇仅补报告" if repaired else ""
        console.print(
            f"\n分析完成：{ok} 成功，{fail} 失败，共用约 {total_tokens} tokens{extra}"
            f"（产物在 data/knowledge/ 下）"
        )
    finally:
        lib.close()


@app.command()
def plan(
    ids: list[str] = typer.Argument(..., help="论文 id 片段（须已分析，experiment.json 存在）"),
    force: bool = typer.Option(False, "--force", "-f", help="已存在复现方案时重新生成"),
) -> None:
    """复现规划：experiment.json + 复现资产 → repro_plan.md（二期复现链第一步）。

    产出「复现目标 / 资产评估 / 环境搭建 / 实施步骤 / 指标比对 / 风险缺口」，
    只做规划不执行代码；不改变论文生命周期状态。
    """
    from paper_agent.services.repro import generate_repro_plan, repro_plan_path

    cfg = load_config()
    lib = _library(cfg)
    try:
        targets = _resolve(lib, ids)
        if not targets:
            console.print("没有匹配的论文。")
            return
        ok = fail = skip = 0
        total_tokens = 0
        for rec in targets:
            sid = rec.paper.source_id
            out = repro_plan_path(cfg, sid)
            if out.exists() and not force:
                console.print(f"{WARN} [dim]{sid}[/dim] 已有复现方案（--force 重新生成）：{out}")
                skip += 1
                continue
            try:
                console.print(f"⏳ [dim]{sid}[/dim] {rec.paper.title[:36]}… 生成复现方案…")
                _plan, usage = generate_repro_plan(sid, cfg=cfg, lib=lib)
                tokens = usage["prompt"] + usage["completion"]
                total_tokens += tokens
                console.print(f"{OK} [dim]{sid}[/dim] {rec.paper.title[:36]}…（~{tokens} tokens）→ {out}")
                ok += 1
            except ValueError as exc:
                console.print(f"{FAIL} [dim]{sid}[/dim] {exc}")
                fail += 1
            except Exception as exc:
                console.print(f"{FAIL} [dim]{sid}[/dim] {str(exc)[:160]}")
                fail += 1
        extra = f"，{skip} 篇跳过" if skip else ""
        console.print(f"\n复现规划完成：{ok} 成功，{fail} 失败{extra}，共约 {total_tokens} tokens")
    finally:
        lib.close()


@app.command()
def index(
    ids: list[str] = typer.Argument(None, help="论文 id 片段；缺省索引 parsed/analyzed 状态论文"),
    all: bool = typer.Option(False, "--all", help="索引全部可索引内容（含仅摘要论文）"),
    force: bool = typer.Option(False, "--force", help="已在向量库的论文也重建索引"),
) -> None:
    """结构感知分块 → 向量化 → Chroma 增量索引。

    已解析全文的论文按全文分块；无全文但有摘要的论文把摘要作为单块入库
    （不改变生命周期状态，后续下载成功可再升级为全文索引）。
    indexed 严格表示「分析+索引都完成」：parsed（分析失败/未跑）保持原状，
    `pa analyze` 重试仍能扫到。
    """
    cfg = load_config()
    lib = _library(cfg)
    try:
        if ids:
            targets = _resolve(lib, ids)
        elif all:
            targets = lib.list()
        else:
            targets = lib.list(status="parsed") + lib.list(status="analyzed")
        if not targets:
            console.print("没有可索引的论文（需先 parse，或论文带摘要）。")
            return
        store = VectorStore(_data_dir(cfg) / "db")
        existing = store.paper_modes()
        ok = skip = 0
        for rec in targets:
            sid, p = rec.paper.source_id, rec.paper
            parsed_path = _parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
            if parsed_path.exists():
                mode = "fulltext"
            elif p.abstract.strip():
                mode = "abstract"
            else:
                console.print(f"{WARN} [dim]{sid}[/dim] {p.title[:36]}…：无全文也无摘要，跳过")
                continue
            if existing.get(sid) == mode and not force:
                # 先索引后分析的时序：向量库已有全文的 analyzed 论文补推进状态
                if mode == "fulltext" and rec.status == "analyzed":
                    lib.set_status(sid, "indexed", error=None)
                skip += 1
                continue
            if mode == "fulltext":
                markdown = parsed_path.read_text(encoding="utf-8")
                chunks = split_markdown(markdown, sid, p.title)
            else:
                chunks = chunk_abstract(sid, p.title, p.abstract)
            if not chunks:
                console.print(f"{FAIL} [dim]{sid}[/dim] {p.title[:36]}…：分块结果为空")
                continue
            try:
                vectors = embed([c.text for c in chunks])
            except RuntimeError as exc:
                console.print(f"{FAIL} {exc}")
                raise typer.Exit(code=1) from exc
            metas = [{**c.metadata, "mode": mode} for c in chunks]
            store.upsert_paper_chunks(sid, [c.text for c in chunks], metas, vectors)
            # 仅对 analyzed 推进状态；parsed（分析失败/未跑）保持原状
            if mode == "fulltext" and rec.status == "analyzed":
                lib.set_status(sid, "indexed", error=None)
            console.print(
                f"{OK} [dim]{sid}[/dim] {p.title[:36]}…（{len(chunks)} 块，{'全文' if mode == 'fulltext' else '仅摘要'}）"
            )
            ok += 1
        extra = f"，{skip} 篇已索引自动跳过（--force 重建）" if skip else ""
        console.print(f"\n索引完成：{ok} 篇入库，向量库共 {store.count()} 块{extra}")
    finally:
        lib.close()
