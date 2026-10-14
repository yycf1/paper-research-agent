"""小型个人库的 BM25 + RRF；复用 Chroma 文档，支持中英文。"""
from __future__ import annotations

from collections import Counter
import math
import re

from paper_agent.rag.vectorstore import Hit


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", text.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(run if len(run) == 1 else (run[i:i+2] for i in range(len(run)-1)))
    return words


def bm25(query: str, corpus: list[Hit], k: int) -> list[Hit]:
    terms = set(tokens(query))
    if not corpus or not terms:
        return []
    docs = [Counter(tokens(h.text)) for h in corpus]
    lengths = [sum(d.values()) for d in docs]
    avg = max(1.0, sum(lengths) / len(docs))
    df = Counter(t for d in docs for t in terms if t in d)
    scores = []
    for i, (doc, length) in enumerate(zip(docs, lengths)):
        score = 0.0
        for t in terms:
            freq = doc[t]
            if freq:
                idf = math.log(1 + (len(docs) - df[t] + .5) / (df[t] + .5))
                score += idf * freq * 2.5 / (freq + 1.5 * (.25 + .75 * length / avg))
        if score > 0:
            scores.append((score, i))
    return [corpus[i] for _, i in sorted(scores, key=lambda x: (-x[0], x[1]))[:k]]


def fuse(*rankings: list[Hit], k: int) -> list[Hit]:
    scores: dict[str, float] = {}
    hits: dict[str, Hit] = {}
    for ranking in rankings:
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            key = hit.chunk_id or f"{hit.paper_id}:{hit.section}:{hit.page}:{hit.text}"
            if key in seen:
                continue
            seen.add(key)
            hits.setdefault(key, hit)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return [hits[key] for key in sorted(scores, key=lambda key: -scores[key])[:k]]
