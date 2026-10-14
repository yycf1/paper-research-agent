"""Explicit offline/live evidence diagnostics; existing pa eval remains unchanged."""

from __future__ import annotations

import copy
import json
from enum import Enum
from pathlib import Path

import typer

from paper_agent import paths
from paper_agent.cli.core import app, console
from paper_agent.config import PROJECT_ROOT, load_config
from paper_agent.rag.diagnostics import (
    apply_reviews,
    corpus_fingerprint,
    load_cases,
    load_reviews,
    load_snapshot,
    run_diagnostics,
    write_report,
    write_snapshot,
)
from paper_agent.rag.embedder import embed
from paper_agent.rag.qa import Retriever
from paper_agent.rag.vectorstore import VectorStore


class Mode(str, Enum):
    bm25 = "bm25"
    vector = "vector"
    hybrid = "hybrid"
    hybrid_rerank = "hybrid-rerank"


class SnapshotStore:
    def __init__(self, hits):
        self.hits = hits

    def all_hits(self, *, paper_id=None):
        return [h for h in self.hits if paper_id is None or h.paper_id == paper_id]

    def count(self):
        return len(self.hits)


@app.command("evidence-snapshot")
def evidence_snapshot(
    output: Path = typer.Option(PROJECT_ROOT / "data/evidence-eval/corpus.json", "--output"),
    overwrite: bool = typer.Option(False, "--overwrite", help="替换已有文本快照"),
) -> None:
    """导出已有索引的文本与版本摘要，不调用模型服务。包含本地文献内容。"""
    if output.suffix != ".json" or (output.exists() and not overwrite):
        console.print("需要 .json 输出；已有文件仅在显式 --overwrite 时替换。")
        raise typer.Exit(2)
    cfg = load_config()
    hits = VectorStore(paths.vector_dir(cfg)).all_hits()
    write_snapshot(output, hits)
    console.print(f"已导出 {len(hits)} 块：{output}")


@app.command("eval-evidence")
def eval_evidence(
    dataset: Path = typer.Option(PROJECT_ROOT / "eval/evidence_cases.json", "--dataset"),
    output: Path = typer.Option(PROJECT_ROOT / "data/evidence-eval/latest.json", "--output"),
    snapshot: Path | None = typer.Option(None, "--snapshot", help="使用文本快照；仅适用于 BM25"),
    mode: Mode = typer.Option(Mode.bm25, "--mode"),
    live: bool = typer.Option(False, "--live", help="允许调用已配置的模型服务，可能产生费用"),
    with_answers: bool = typer.Option(False, "--with-answers", help="同时生成回答；需要 --live"),
    reviews: Path | None = typer.Option(None, "--reviews", help="绑定本次产物摘要的人工复核文件"),
    k: int = typer.Option(8, "--k", min=1, max=100),
    limit: int | None = typer.Option(None, "--limit", min=1),
) -> None:
    """逐阶段证据诊断；默认只运行离线 BM25，不将未复核答案计为正确。"""
    if not live and (mode != Mode.bm25 or with_answers):
        console.print("向量/重排/答案生成需要显式 --live；默认不会调用模型服务。")
        raise typer.Exit(2)
    if snapshot is not None and mode != Mode.bm25:
        console.print("文本快照不含向量，仅支持 --mode bm25。")
        raise typer.Exit(2)
    inputs = {p.resolve() for p in (dataset, snapshot, reviews) if p is not None}
    if output.suffix != ".json" or any(p in inputs for p in (output.resolve(), output.with_suffix(".md").resolve())):
        console.print("输出需要独立的 .json 路径，不能覆盖输入。")
        raise typer.Exit(2)
    try:
        data = load_cases(dataset)
        checked_reviews = load_reviews(reviews)
        cfg = copy.deepcopy(load_config())
        if snapshot is not None:
            corpus = load_snapshot(snapshot)
            store = SnapshotStore(corpus)
        else:
            source_store = VectorStore(paths.vector_dir(cfg))
            corpus = source_store.all_hits()
            store = SnapshotStore(corpus) if mode == Mode.bm25 else source_store
        cfg.setdefault("retrieval", {})["mode"] = "hybrid" if mode == Mode.hybrid_rerank else mode.value
        cfg.setdefault("reranker", {})["enabled"] = mode == Mode.hybrid_rerank
        retriever = Retriever(store, cfg=cfg, embed_fn=lambda texts: embed(texts, cfg=cfg))
        report = run_diagnostics(
            data, corpus, retriever, k=k, with_answers=with_answers, reviews=checked_reviews, limit=limit
        )
        if mode != Mode.bm25 and corpus_fingerprint(store.all_hits()) != report["corpus_sha256"]:
            raise ValueError("Corpus changed during live evaluation; rerun on a stable index")
        report["run_configuration"] = {
            "mode": mode.value,
            "live_allowed": live,
            "candidate_k": cfg["retrieval"].get("candidate_k", 20),
            "configured_embedding_model": cfg.get("embedding", {}).get("model"),
            "configured_reranker_model": cfg.get("reranker", {}).get("model"),
            "configured_chat_model": cfg.get("llm", {}).get("model"),
            "model_provenance": "configuration_only_not_index_build_provenance",
        }
        md_path = write_report(output, report)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        console.print(f"评测输入或运行条件不满足：{type(exc).__name__}")
        raise typer.Exit(2) from None
    console.print(
        f"完成 {report['cases_run']} 题；检索评分 {report['cases_scored']} 题；"
        f"人工复核答案 {report['reviewed_answers']} 题。"
    )
    console.print(f"JSON：{output}\n阅读报告：{md_path}")
    if report["cases_scored"] == 0:
        console.print("没有可评分证据标签，请检查语料版本与标签状态；报告未生成虚构分数。")


@app.command("review-evidence")
def review_evidence(
    report: Path = typer.Option(..., "--report"),
    reviews: Path = typer.Option(..., "--reviews"),
    output: Path = typer.Option(..., "--output"),
) -> None:
    """对已保存的答案附加人工复核，不重新检索或调用模型。"""
    if output.suffix != ".json" or any(
        p.resolve() in {report.resolve(), reviews.resolve()} for p in (output, output.with_suffix(".md"))
    ):
        console.print("输出需要独立的 .json 路径，不能覆盖输入。")
        raise typer.Exit(2)
    try:
        saved = json.loads(report.read_text(encoding="utf-8"))
        updated = apply_reviews(saved, load_reviews(reviews))
        write_report(output, updated)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        console.print(f"无法附加复核：{type(exc).__name__}")
        raise typer.Exit(2) from None
    console.print(f"已复核 {updated['reviewed_answers']} 个答案：{output}")
