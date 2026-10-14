"""单篇全链处理服务：download → parse → experiment → report → index，按状态断点续跑。

与 pipeline 的批量扫描不同，这里指定一篇论文从当前状态推进到 indexed；
每一步失败即停并保留 error（前端可重试），语义与 CLI 各命令一致。

M18 重构（处理透明化）：阶段结构化为 stages 列表（含 running/ok/skipped/failed
与耗时），progress 回调在每次状态变化时被调用——API 层把它接到 job.result 的
原地更新上，前端轮询立刻可见。旧版把步骤攒在内存里最后一次性返回，两个 LLM
调用的一两分钟里界面只能转圈。实验结构与精读报告拆为两个独立阶段，且
experiment.json 支持产物级复用（内容与契约摘要）：报告失败重试不重复提取有效结构。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from paper_agent import paths
from paper_agent.download import download_pdf, safe_dirname
from paper_agent.extraction.experiment_flow import extract_experiment
from paper_agent.extraction.report import generate_report
from paper_agent.library import Library
from paper_agent.llm import chat
from paper_agent.parsing import extract_markdown, readable_chars
from paper_agent.rag.embedder import embed
from paper_agent.rag.splitter import split_markdown
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.services.versions import (
    EXPERIMENT_CONTRACT,
    REPORT_CONTRACT,
    artifact_current,
    file_digest,
    index_current,
    index_metadata,
    parser_contract,
    save_artifact,
)

STAGE_LABELS = {
    "download": "下载全文",
    "parse": "解析文本",
    "experiment": "提取实验结构",
    "report": "生成精读报告",
    "index": "向量索引",
}


def process_paper(sid: str, cfg: dict, lib: Library, *, progress: Callable | None = None) -> dict:
    """把一篇论文从当前状态推进到 indexed，返回阶段记录与最终状态。

    progress(stages) 在每次阶段状态变化时被调用（stages 为完整列表快照，
    元素含 stage/label/status/detail/duration_ms）；缺省 None 保持静默。
    """
    rec = lib.get(sid)
    if rec is None:
        raise ValueError(f"未找到论文：{sid}")
    out: dict = {"source_id": sid, "title": rec.paper.title, "stages": []}
    stages = out["stages"]
    _t0 = 0.0  # 当前阶段起始时刻（阶段顺序执行，单变量即可）

    def _notify() -> None:
        if progress is not None:
            progress(stages)

    def _stage(key: str) -> dict:
        nonlocal _t0
        _t0 = time.monotonic()
        st = {"stage": key, "label": STAGE_LABELS[key], "status": "running",
              "detail": "", "duration_ms": 0}
        stages.append(st)
        _notify()
        return st

    def _end(st: dict, status: str, detail: str = "") -> None:
        st["status"] = status
        st["detail"] = detail
        st["duration_ms"] = int((time.monotonic() - _t0) * 1000)
        _notify()

    # 1. 下载（download_failed 也重走下载：失败重试是断点续跑的一部分）
    rec = lib.get(sid)
    st = _stage("download")
    if rec.status in ("discovered", "download_failed"):
        if not rec.paper.pdf_urls:
            _end(st, "failed", "无 OA 全文链接（仅摘要），无法处理")
            out["final_status"] = rec.status
            return out
        try:
            path = download_pdf(
                rec.paper, paths.papers_dir(cfg), proxy=cfg.get("proxy", "") or ""
            )
            lib.set_status(sid, "downloaded", error=None, pdf_path=str(path))
            _end(st, "ok", f"{path.stat().st_size / 1024:.0f} KB")
        except Exception as exc:
            lib.set_status(sid, "download_failed", error=str(exc)[:500])
            _end(st, "failed", str(exc)[:200])
            out["final_status"] = "download_failed"
            return out
    else:
        _end(st, "skipped", "已有原文，跳过")

    # 2. 解析
    rec = lib.get(sid)
    st = _stage("parse")
    parsed_path = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
    if rec.pdf_path and Path(rec.pdf_path).exists() and rec.status != "downloaded":
        if not artifact_current(parsed_path, file_digest(Path(rec.pdf_path)), cfg, parser_contract()):
            lib.set_status(sid, "downloaded", error="原文或解析契约已变化，重新解析")
            rec = lib.get(sid)
    if rec.status == "downloaded":
        min_chars = 30 if (rec.pdf_path or "").lower().endswith(".docx") else 200
        try:
            md = extract_markdown(Path(rec.pdf_path))
        except Exception as exc:
            lib.set_status(sid, "downloaded", error=f"解析失败：{exc}"[:500])
            _end(st, "failed", str(exc)[:200])
            out["final_status"] = "downloaded"
            return out
        if readable_chars(md) < min_chars:
            lib.set_status(sid, "downloaded", error="解析结果近乎为空（可能是扫描版 PDF）")
            _end(st, "failed", "解析结果近乎为空")
            out["final_status"] = "downloaded"
            return out
        out_md = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
        out_md.parent.mkdir(parents=True, exist_ok=True)
        save_artifact(out_md, md, file_digest(Path(rec.pdf_path)), cfg, parser_contract())
        lib.set_status(sid, "parsed", error=None)
        _end(st, "ok", f"{len(md)} 字符")
    else:
        _end(st, "skipped", "已解析")

    # 3+4. 知识提取：实验结构与精读报告拆为两个阶段，各自失败各自停
    rec = lib.get(sid)
    md_path = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
    out_dir = paths.knowledge_dir(cfg) / safe_dirname(sid)
    if rec.status in ("analyzed", "indexed") and md_path.exists():
        markdown = md_path.read_text(encoding="utf-8")
        if not (artifact_current(out_dir / "experiment.json", markdown, cfg, EXPERIMENT_CONTRACT)
                and artifact_current(out_dir / "report.md", markdown, cfg, REPORT_CONTRACT)):
            lib.set_status(sid, "parsed", error="知识产物过期或版本未登记，需重新分析")
            rec = lib.get(sid)
    if rec.status == "parsed":
        md_path = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
        markdown = md_path.read_text(encoding="utf-8")
        out_dir = paths.knowledge_dir(cfg) / safe_dirname(sid)

        st = _stage("experiment")
        exp_path = out_dir / "experiment.json"
        if artifact_current(exp_path, markdown, cfg, EXPERIMENT_CONTRACT):
            # 产物级复用：上次结构提取成功而报告失败时，重试不重复花这份 token
            experiment = json.loads(exp_path.read_text(encoding="utf-8"))
            _end(st, "skipped", "复用上次提取结果（未过期）")
        else:
            try:
                experiment = extract_experiment(markdown, chat_fn=chat)
            except Exception as exc:
                lib.set_status(sid, "parsed", error=f"分析失败：{exc}"[:500])
                _end(st, "failed", str(exc)[:200])
                out["final_status"] = "parsed"
                return out
            out_dir.mkdir(parents=True, exist_ok=True)
            save_artifact(exp_path, json.dumps(experiment, ensure_ascii=False, indent=2),
                          markdown, cfg, EXPERIMENT_CONTRACT)
            _end(st, "ok", "experiment.json")

        st = _stage("report")
        try:
            report, _report_usage = generate_report(markdown, chat_fn=chat)
        except Exception as exc:
            lib.set_status(sid, "parsed", error=f"分析失败：{exc}"[:500])
            _end(st, "failed", str(exc)[:200])
            out["final_status"] = "parsed"
            return out
        out_dir.mkdir(parents=True, exist_ok=True)
        save_artifact(out_dir / "report.md", report, markdown, cfg, REPORT_CONTRACT)
        lib.set_status(sid, "analyzed", error=None)
        _end(st, "ok", "report.md")
    else:
        st = _stage("experiment")
        _end(st, "skipped", "已分析")
        st = _stage("report")
        _end(st, "skipped", "已分析")

    # 5. 索引
    rec = lib.get(sid)
    st = _stage("index")
    md_path = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
    store = VectorStore(paths.vector_dir(cfg))
    try:
        if not md_path.exists():
            raise ValueError("解析文本缺失，不能声明索引就绪")
        markdown = md_path.read_text(encoding="utf-8")
        chunks = split_markdown(markdown, sid, rec.paper.title)
        if not chunks:
            raise ValueError("无可索引内容，不能声明索引就绪")
        metas = index_metadata(markdown, chunks, cfg, "fulltext")
        if index_current(store, sid, metas):
            _end(st, "skipped", "索引内容与配置版本一致")
        else:
            vectors = embed([c.text for c in chunks], cfg=cfg)
            store.upsert_paper_chunks(sid, [c.text for c in chunks], metas, vectors)
            _end(st, "ok", f"{len(chunks)} 块（版本已更新）")
    except Exception as exc:
        if rec.status == "indexed":
            lib.set_status(sid, "analyzed", error=f"索引失败：{exc}"[:500])
        else:
            lib.set_status(sid, rec.status, error=f"索引失败：{exc}"[:500])
        _end(st, "failed", str(exc)[:200])
        out["final_status"] = lib.get(sid).status
        return out
    # indexed 严格表示「分析+索引都完成」（M6 语义）
    rec = lib.get(sid)
    if rec is not None and rec.status == "analyzed":
        lib.set_status(sid, "indexed", error=None)
    out["final_status"] = lib.get(sid).status
    return out
