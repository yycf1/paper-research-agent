"""问答链：多轮查询改写 → 混合召回 → 重排序 → 带原文证据回答。

设计对应 PLAN §9 M3：
- 出处标注 [编号]（论文标题 + 章节 + 页码），编号与检索块一一对应；
- 提供的片段不足以回答时，明确回答「论文库中的内容未提及」，禁止编造；
- 多轮 REPL 靠 history（近几轮问答）维持语境，检索始终按当前问题执行。
"""

from __future__ import annotations

import logging
import time

from paper_agent.config import load_config
from paper_agent.llm import chat
from paper_agent.rag.embedder import embed
from paper_agent.rag.hybrid import bm25, fuse
from paper_agent.rag.reranker import rerank
from paper_agent.rag.scope import check_hits, query_scope
from paper_agent.rag.tracing import capture_stage, hit_record
from paper_agent.rag.vectorstore import Hit, VectorStore

MAX_HISTORY_TURNS = 3

_PROMPT = """你是论文阅读助手。只依据下方论文片段回答问题，并遵守：
1. 关键结论处用 [编号] 标注出处（编号对应片段序号）；
2. 若片段不足以回答问题，回答「论文库中的内容未提及」，不要用已有一知识编造；
3. 回答简洁，用中文。
4. 必须核对全部片段；直接证据可能出现在后面的片段、英文表格或正文中。只要有明确证据，就提取回答并引用该片段；不要因为第一个片段没写就拒答。

论文片段：
{context}

问题：{question}"""


class Retriever:
    """共享检索入口；关闭 hybrid 可运行纯向量基线。"""

    def __init__(self, store: VectorStore, embed_fn=embed, *, cfg: dict | None = None, rerank_fn=rerank):
        self.store = store
        self.embed_fn = embed_fn
        self.cfg = cfg if cfg is not None else load_config()
        self.rerank_fn = rerank_fn
        self.notes: list[str] = []

    def retrieve(self, query: str, *, k: int = 5, paper_id: str | None = None,
                 paper_ids: list[str] | None = None,
                 trace: dict | None = None) -> list[Hit]:
        scope = query_scope(paper_id, paper_ids)
        self.notes = []
        started = time.perf_counter()
        options = self.cfg.get("retrieval", {})
        hybrid = options.get("hybrid", True) and callable(getattr(self.store, "all_hits", None))
        mode = options.get("mode") or ("hybrid" if hybrid else "vector")
        if mode not in {"vector", "bm25", "hybrid"}:
            raise ValueError(f"Unsupported retrieval mode: {mode}")
        depth = max(k, int(options.get("candidate_k", 20))) if mode != "vector" else k
        if trace is not None:
            trace.clear()
            trace.update(query=query, paper_id=paper_id, mode=mode, requested_k=k, candidate_k=depth,
                         stages={s: [] for s in ("dense", "sparse", "fused", "reranked", "final")},
                         stage_status={s: "not_run" for s in ("dense", "sparse", "fused", "reranked", "final")},
                         timings_ms={}, notes=[], errors={})
            if paper_ids is not None:
                trace["paper_ids"] = scope["paper_ids"]
        if not query.strip() or k < 1:
            return []
        sparse = []
        if mode in {"bm25", "hybrid"}:
            stage_started = time.perf_counter()
            corpus = self.store.all_hits(**scope)
            check_hits(corpus, scope)
            sparse = bm25(query, corpus, depth)
            capture_stage(trace, "sparse", sparse, started=stage_started)
        dense = []
        if mode != "bm25":
            stage_started = time.perf_counter()
            try:
                vector = self.embed_fn([query])[0]
                dense = self.store.query(vector, k=depth, **scope)
                check_hits(dense, scope)
                capture_stage(trace, "dense", dense, started=stage_started)
            except Exception as exc:
                capture_stage(trace, "dense", [], status="error", started=stage_started)
                if trace is not None:
                    trace["errors"]["dense"] = type(exc).__name__
                if not sparse:
                    raise
                self.notes.append("向量检索不可用，本次降级为 BM25 关键词检索。")
        if mode == "hybrid":
            stage_started = time.perf_counter()
            hits = fuse(dense, sparse, k=depth)
            capture_stage(trace, "fused", hits, started=stage_started)
        else:
            hits = sparse if mode == "bm25" else dense
        rcfg = self.cfg.get("reranker", {})
        if mode == "hybrid" and rcfg.get("enabled", False) and hits:
            stage_started = time.perf_counter()
            try:
                hits = self.rerank_fn(query, hits, rcfg, k=k)
                capture_stage(trace, "reranked", hits, started=stage_started)
            except Exception as exc:
                capture_stage(trace, "reranked", [], status="error", started=stage_started)
                if trace is not None:
                    trace["errors"]["reranked"] = type(exc).__name__
                self.notes.append("重排序服务不可用，本次使用召回融合排序。")
                logging.getLogger(__name__).warning("Reranker fallback: %s", type(exc).__name__)
        check_hits(hits, scope)
        capture_stage(trace, "final", hits[:k])
        if trace is not None:
            trace["notes"] = list(self.notes)
            trace["timings_ms"]["total"] = round((time.perf_counter() - started) * 1000, 3)
        return hits[:k]


def build_context(hits: list[Hit]) -> str:
    """检索命中 → 带编号的上下文块。"""
    blocks = []
    for i, h in enumerate(hits, start=1):
        origin = f"论文《{h.title}》· {h.section}"
        if h.page:
            origin += f" · 第{h.page}页"
        blocks.append(f"[{i}] {origin}\n{h.text}")
    return "\n\n".join(blocks)


def sources_from_hits(hits: list[Hit]) -> list[dict]:
    """检索命中 → 出处列表，编号与答案中的 [n] 引用对齐。

    答案里的 [n] 指片段序号；同一章节的多个片段合并为一条出处，
    n 收集该章节全部片段号（如 [1][3]）——否则正文引用与出处清单对不上。
    """
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for i, h in enumerate(hits, start=1):
        key = f"{h.paper_id}:{h.section}"
        if key not in grouped:
            grouped[key] = {
                "n": [i],
                "paper_id": h.paper_id,
                "title": h.title,
                "section": h.section,
                "page": h.page,
                "chunks": [],
            }
            order.append(key)
        else:
            grouped[key]["n"].append(i)
        grouped[key]["chunks"].append({"n": i, "chunk_id": h.chunk_id,
                                       "page": h.page, "text": h.text})
    return [grouped[k] for k in order]


def answer_question(
    question: str,
    *,
    retriever: Retriever,
    history: list[tuple[str, str]] | None = None,
    k: int = 8,
    paper_id: str | None = None,
    paper_ids: list[str] | None = None,
    chat_fn=chat,
    hits: list[Hit] | None = None,
    trace: dict | None = None,
) -> tuple[str, list[Hit]]:
    """回答一个问题，返回 (答案文本, 使用的检索命中)。

    hits 可外部传入（评测时检索与判定共用一次结果）；history 为近几轮
    (问题, 回答)，用于 REPL 里的指代消解。k 默认 8：块均约 600 token，
    8 块 ≈ 5k token 上下文，实测关键事实多排在 6~8 位，5 块容易漏。
    """
    scope = query_scope(paper_id, paper_ids)
    if trace is not None:
        trace.update(question=question, retrieval_query=question, rewrite="not_run", calls=[])
        original_chat = chat_fn

        def chat_fn(messages, **kwargs):
            started = time.perf_counter()
            call = {"phase": trace.get("phase", "answer")}
            trace["calls"].append(call)
            try:
                reply, usage = original_chat(messages, **kwargs)
                call["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                                 if isinstance(usage.get(key), int) and not isinstance(usage[key], bool)}
                call["status"] = "ok"
                return reply, usage
            except Exception as exc:
                call.update(status="error", error_type=type(exc).__name__)
                raise
            finally:
                call["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)

    def finish(answer, used, status):
        if trace is not None:
            trace.update(answer=answer, answer_status=status, context=[hit_record(h) for h in used])
            trace.pop("phase", None)
        return answer, used

    if hits is None:
        query = question
        if history and callable(getattr(retriever.store, "all_hits", None)):
            # 仅改写检索查询，最终回答始终使用用户原问题与原会话。
            try:
                if trace is not None:
                    trace["phase"] = "rewrite"
                rewritten, _ = chat_fn([{"role": "user", "content": (
                    "将最后的问题改写为独立的文献检索查询，消解它/该方法等指代。"
                    "保留论文名、方法名与限制条件，不回答、不增加事实，只输出查询。\n"
                    + "\n".join(f"问：{q}\n答：{a[:1200]}" for q, a in history[-3:])
                    + f"\n最后的问题：{question}"
                )}], max_tokens=256, temperature=0.0)
                if rewritten.strip():
                    query = rewritten.strip()[:1000]
                if trace is not None:
                    trace["rewrite"] = "applied" if rewritten.strip() else "empty_fallback"
            except Exception:
                query = question
                if trace is not None:
                    trace["rewrite"] = "error_fallback"
        if trace is None:
            hits = retriever.retrieve(query, k=k, **scope)
        else:
            trace["retrieval_query"] = query
            hits = retriever.retrieve(query, k=k, **scope, trace=trace.setdefault("retrieval", {}))
    check_hits(hits, scope)
    if not hits:
        return finish("向量库还没有内容：请先运行 `pa index` 建立索引。", [], "no_hits")
    threshold = retriever.cfg.get("retrieval", {}).get("min_rerank_score", 0.1)
    if all(h.relevance_score is not None for h in hits) and max(h.relevance_score for h in hits) < threshold:
        return finish("论文库中的内容未提及：检索到的证据相关性不足，无法可靠回答。", [], "low_evidence")
    messages: list[dict] = []
    for prev_q, prev_a in (history or [])[-MAX_HISTORY_TURNS:]:
        messages.append({"role": "user", "content": prev_q})
        messages.append({"role": "assistant", "content": prev_a})
    messages.append(
        {"role": "user", "content": _PROMPT.format(context=build_context(hits), question=question)}
    )
    if trace is not None:
        trace["phase"] = "answer"
    answer, _usage = chat_fn(messages, max_tokens=2048, temperature=0.2)
    if not answer.strip():
        # GLM 混合推理模型：思考 tokens 可能吃光 max_tokens 导致空回复，
        # 禁用思考按同参数重问一次（正常路径零额外开销）
        answer, _usage = chat_fn(messages, max_tokens=2048, temperature=0.2, thinking=False)
    elif "未提及" in answer and any((h.relevance_score or 0) >= 0.35 for h in hits):
        # 高相关证据仍被模型拒答时，仅复核一次；绝不将拒答直接改写成肯定答案。
        review = messages + [{"role": "assistant", "content": answer}, {"role": "user", "content": (
            "请重新核对上方全部编号片段中的直接证据（包括英文正文与表格）。"
            "如果能够回答原问题，给出简短答案和准确引用；若确实没有证据，仍回答未提及。"
        )}]
        try:
            reviewed, _usage = chat_fn(review, max_tokens=2048, temperature=0.0, thinking=True)
            if reviewed.strip():
                answer = reviewed
        except Exception:
            pass  # 复核失败保留安全拒答，不把可用回复升级为服务错误
    answer = answer.strip() or "论文库中的内容未提及：模型未返回有效回答，请重试。"
    if retriever.notes:
        answer += "\n\n> " + " ".join(retriever.notes)
    return finish(answer, hits, "generated")
