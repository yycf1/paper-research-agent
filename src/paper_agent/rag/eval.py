"""RAG 评测：检索命中率与（可选的）答案关键词命中率。

评测集见 eval/evalset.json：每条 case 带期望出处（章节名子串，与向量库
metadata.section 匹配）与期望关键词组（组内 OR、组间 AND）。判定函数
保持纯逻辑，便于离线测试；检索/生成由 CLI 命令注入。

M15 起增加排序指标：hit@k 曲线（k=1/3/5/8）、MRR、nDCG@8——
「命中率 100%」不说明排序好，MRR/nDCG 才能反映相关块排多前，
换分块/换 embedding/换模型时的劣化能更早暴露。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

RANK_KS = (1, 3, 5, 8)


def load_evalset(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError(f"评测集 {path} 缺少非空 cases 数组")
    for i, case in enumerate(data["cases"]):
        for key in ("id", "question"):
            if not case.get(key):
                raise ValueError(f"评测集第 {i} 条缺少 {key}")
    return data


def hit_relevant(case: dict, hit) -> bool:
    """单命中判定：命中块的章节是否匹配任一期望章节（子串、忽略大小写）。"""
    sections = case.get("expect_sections") or []
    section_low = hit.section.lower()
    return any(s.lower() in section_low for s in sections)


def judge_retrieval(case: dict, hits: list) -> bool | None:
    """期望章节（子串、忽略大小写）是否出现在 top-k 命中里。无期望时返回 None。"""
    if not case.get("expect_sections"):
        return None
    return any(hit_relevant(case, h) for h in hits)


def first_relevant_rank(case: dict, hits: list) -> int | None:
    """首个相关命中的排名（1-based）；top-k 内无相关返回 None。"""
    for i, h in enumerate(hits, start=1):
        if hit_relevant(case, h):
            return i
    return None


def rank_metrics(case: dict, hits: list, ks: tuple[int, ...] = RANK_KS) -> dict:
    """单条 case 的排序指标：hit@k、倒数排名 RR、nDCG@max(ks)。

    nDCG 采用二值相关性，理想 DCG 按「top-k 内实际召回的相关块数」计算
    （全库相关总数不可廉价获得时的标准近似——文档化在此，解读时注意）。
    """
    rank = first_relevant_rank(case, hits)
    out = {f"hit@{k}": bool(rank is not None and rank <= k) for k in ks}
    out["rr"] = 0.0 if rank is None else 1.0 / rank
    k = max(ks)
    top = hits[:k]
    dcg = sum(1.0 / math.log2(i + 1) for i, h in enumerate(top, start=1) if hit_relevant(case, h))
    relevant_in_top = sum(1 for h in top if hit_relevant(case, h))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, relevant_in_top + 1))
    out[f"ndcg@{k}"] = dcg / idcg if idcg else 0.0
    return out


def aggregate_rank_metrics(rows: list[dict]) -> dict:
    """多条 case 的指标聚合：hit@k 均值、MRR、nDCG 均值。"""
    if not rows:
        return {}
    out: dict = {}
    for key in rows[0]:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float, bool))]
        if values:
            out[key] = sum(bool(v) if key.startswith("hit@") else float(v) for v in values) / len(values)
    return out


def judge_answer(case: dict, answer: str) -> bool | None:
    """关键词组判定：组间 AND、组内 OR（忽略大小写）。无期望时返回 None。"""
    groups = case.get("expect_keywords")
    if not groups:
        return None
    low = answer.lower()
    return all(any(alt.lower() in low for alt in group) for group in groups)
