"""Host-owned paper scope. Model arguments can narrow it, never widen it."""

from __future__ import annotations

from paper_agent.library import Library

MAX_SCOPE_PAPERS = 6


def resolve_paper(lib: Library, value: str, *, allow_missing: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("论文 ID 必须是非空字符串")
    sid = value.strip()
    if lib.get(sid) is not None:
        return sid
    if ":" not in sid:
        matches = [pid for pid in lib.find(sid) if pid.split(":", 1)[-1] == sid]
        if len(matches) > 1:
            raise ValueError(f"论文 ID {sid} 匹配多篇，请使用完整 source_id：{', '.join(matches)}")
        if matches:
            return matches[0]
    if allow_missing:
        return sid
    raise ValueError(f"未找到论文：{sid}")


def resolve_papers(lib: Library, values: list[str], *, allow_missing: bool = False) -> tuple[str, ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_SCOPE_PAPERS:
        raise ValueError(f"paper_ids 必须是包含 1~{MAX_SCOPE_PAPERS} 个论文 ID 的数组")
    return tuple(dict.fromkeys(resolve_paper(lib, sid, allow_missing=allow_missing) for sid in values))


class PaperScope:
    def __init__(self, lib: Library, *, anchor: str | None = None,
                 allowed_paper_ids: list[str] | None = None):
        self.lib = lib
        self.anchor = resolve_paper(lib, anchor) if anchor is not None else None
        allowed = resolve_papers(lib, allowed_paper_ids) if allowed_paper_ids is not None else None
        if self.anchor:
            if allowed is not None and allowed != (self.anchor,):
                raise ValueError("anchor 与 allowed_paper_ids 冲突；锚定任务只能包含该论文")
            allowed = (self.anchor,)
        self.allowed = allowed

    def one(self, value: str) -> str:
        sid = resolve_paper(self.lib, value)
        self.check((sid,))
        return sid

    def many(self, values: list[str], *, allow_missing: bool = False) -> tuple[str, ...]:
        ids = resolve_papers(self.lib, values, allow_missing=allow_missing and self.allowed is None)
        self.check(ids)
        return ids

    def check(self, ids: tuple[str, ...]) -> None:
        if self.allowed is not None and any(sid not in self.allowed for sid in ids):
            raise ValueError(f"论文超出任务作用域；只允许：{', '.join(self.allowed)}")

    def query_ids(self, args: dict) -> tuple[str, ...] | None:
        if "paper_id" in args and "paper_ids" in args:
            raise ValueError("paper_id 与 paper_ids 不能同时指定")
        if "paper_id" in args:
            return (self.one(args["paper_id"]),)
        if "paper_ids" in args:
            return self.many(args["paper_ids"])
        return self.allowed
