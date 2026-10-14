"""SiliconFlow 兼容重排序接口；失败由调用方降级为融合排序。"""
from __future__ import annotations

from dataclasses import replace
import math
import httpx

from paper_agent.config import resolve_endpoint
from paper_agent.rag.vectorstore import Hit


def rerank(query: str, hits: list[Hit], config: dict, *, k: int) -> list[Hit]:
    ep = resolve_endpoint(config)
    if not ep.api_key:
        raise RuntimeError("未配置 Reranker API Key")
    response = httpx.post(
        ep.base_url.rstrip("/") + "/rerank",
        headers={"Authorization": f"Bearer {ep.api_key}"},
        json={"model": ep.model, "query": query, "documents": [h.text for h in hits],
              "top_n": min(k, len(hits)), "return_documents": False},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()["results"]
    ranked, seen = [], set()
    for row in rows:
        i, score = row["index"], float(row["relevance_score"])
        if not isinstance(i, int) or not 0 <= i < len(hits) or i in seen or not math.isfinite(score):
            raise ValueError("重排序返回无效的块索引或分数")
        seen.add(i)
        ranked.append(replace(hits[i], relevance_score=score))
    if len(ranked) != min(k, len(hits)):
        raise ValueError("重排序返回不完整")
    return sorted(ranked, key=lambda h: -h.relevance_score)
