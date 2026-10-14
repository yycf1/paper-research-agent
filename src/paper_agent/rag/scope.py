"""Shared filters and fail-closed checks for both retrieval branches."""

from __future__ import annotations


def query_scope(paper_id: str | None = None, paper_ids: list[str] | tuple[str, ...] | None = None) -> dict:
    if paper_id is not None and paper_ids is not None:
        raise ValueError("paper_id 与 paper_ids 不能同时指定")
    if paper_id is not None:
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise ValueError("paper_id 不能为空")
        return {"paper_id": paper_id}
    if paper_ids is not None:
        if not isinstance(paper_ids, (list, tuple)) or not paper_ids:
            raise ValueError("paper_ids 必须是非空数组，不能以空数组扩大为全库查询")
        if any(not isinstance(sid, str) or not sid.strip() for sid in paper_ids):
            raise ValueError("paper_ids 中的 ID 必须是非空字符串")
        return {"paper_ids": list(dict.fromkeys(paper_ids))}
    return {"paper_id": None}


def where_scope(paper_id=None, paper_ids=None):
    scope = query_scope(paper_id, paper_ids)
    if "paper_ids" in scope:
        return {"paper_id": {"$in": scope["paper_ids"]}}
    return {"paper_id": {"$eq": paper_id}} if paper_id is not None else None


def check_hits(hits, scope: dict) -> None:
    allowed = scope.get("paper_ids")
    if allowed is None and scope.get("paper_id") is not None:
        allowed = [scope["paper_id"]]
    if allowed is not None and any(h.paper_id not in allowed for h in hits):
        raise RuntimeError("检索返回了任务范围之外的论文；已停止使用这些证据")
