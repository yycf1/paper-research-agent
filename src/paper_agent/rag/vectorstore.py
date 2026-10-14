"""Chroma 向量库封装：本地持久化 + 按论文整体覆盖的增量索引。

增量语义：一篇论文的块集合视为一个版本——重建索引时先按 paper_id 删旧
再插新，保证重复执行幂等；块 id 形如 `{paper_id}::{chunk_index}`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# Chroma publishes a process-wide system before its native bindings finish starting.
# Concurrent first requests must not obtain that partially initialized system.
_CLIENT_INIT_LOCK = Lock()


@dataclass
class Hit:
    """一条检索命中。"""

    paper_id: str
    title: str
    section: str
    page: int
    text: str
    distance: float
    chunk_id: str = ""
    relevance_score: float | None = None


class VectorStore:
    """papers 集合的读写入口。查询侧只依赖 embedding，不内置嵌入模型。"""

    def __init__(self, path: Path):
        import chromadb

        path.mkdir(parents=True, exist_ok=True)
        # Windows HNSW 原生读取器无法重新打开中文绝对路径；相同文件使用
        # ASCII 相对路径可正确恢复。启动器固定 cwd 为项目根，避免全局 chdir。
        storage_path = str(path.resolve())
        if os.name == "nt" and not storage_path.isascii():
            try:
                relative = os.path.relpath(path.resolve(), Path.cwd())
                if relative.isascii():
                    storage_path = relative
            except ValueError:
                pass  # 不同盘符时保持原路径；可在配置中指定 ASCII 数据目录
        with _CLIENT_INIT_LOCK:
            self.client = chromadb.PersistentClient(path=storage_path)
            self.collection = self.client.get_or_create_collection(
                "papers", metadata={"hnsw:space": "cosine"}
            )

    # ---- 写入 ----

    def upsert_paper_chunks(
        self,
        paper_id: str,
        texts: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
    ) -> int:
        """覆盖式写入一篇论文的全部块，返回写入条数。"""
        self.remove_paper(paper_id)
        if not texts:
            return 0
        ids = [f"{paper_id}::{i}" for i in range(len(texts))]
        self.collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        return len(ids)

    def remove_paper(self, paper_id: str) -> None:
        self.collection.delete(where={"paper_id": {"$eq": paper_id}})

    def count(self) -> int:
        return self.collection.count()

    def paper_modes(self) -> dict[str, str]:
        """库内各 paper_id 已索引的模式（fulltext/abstract），增量索引跳过判断用。

        同一论文混合两种块时以 fulltext 为准（覆盖式写入下不会混，防御性兜底）。
        """
        rows = self.collection.get(include=["metadatas"])
        out: dict[str, str] = {}
        for m in rows["metadatas"]:
            pid, mode = m.get("paper_id", ""), m.get("mode", "")
            if pid and (pid not in out or (out[pid] != "fulltext" and mode == "fulltext")):
                out[pid] = mode
        return out

    # ---- 查询 ----

    def all_hits(self, *, paper_id: str | None = None) -> list[Hit]:
        """BM25 与向量索引共享同一份块，删除/重建无需同步第二份索引。"""
        rows = self.collection.get(
            where={"paper_id": {"$eq": paper_id}} if paper_id else None,
            include=["documents", "metadatas"],
        )
        return [Hit(
            paper_id=m.get("paper_id", ""), title=m.get("title", ""),
            section=m.get("section", ""), page=int(m.get("page", 0)),
            text=doc, distance=1.0, chunk_id=cid,
        ) for cid, doc, m in zip(rows["ids"], rows["documents"], rows["metadatas"])]

    def query(
        self,
        embedding: list[float],
        *,
        k: int = 5,
        paper_id: str | None = None,
    ) -> list[Hit]:
        """按向量取 top-k；paper_id 限定范围时在该论文内检索。"""
        where = {"paper_id": {"$eq": paper_id}} if paper_id else None
        res = self.collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[Hit] = []
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            hits.append(
                Hit(
                    paper_id=meta.get("paper_id", ""),
                    title=meta.get("title", ""),
                    section=meta.get("section", ""),
                    page=int(meta.get("page", 0)),
                    text=doc,
                    distance=float(dist),
                    chunk_id=cid,
                )
            )
        return hits
