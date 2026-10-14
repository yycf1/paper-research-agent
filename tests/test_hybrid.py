from dataclasses import replace

import httpx
import pytest

from paper_agent.rag.hybrid import bm25, fuse
from paper_agent.rag.qa import Retriever, answer_question, sources_from_hits
from paper_agent.rag.reranker import rerank
from paper_agent.rag.splitter import split_markdown, estimate_tokens
from paper_agent.rag.vectorstore import Hit, VectorStore


def hit(cid, text, paper="p1"):
    return Hit(paper, "测试", "方法", 1, text, .3, chunk_id=cid)


def test_bm25_bilingual_and_no_match():
    docs = [hit("a", "graph neural networks 图神经网络"), hit("b", "cooking 食谱")]
    assert bm25("图神经网络", docs, 2)[0].chunk_id == "a"
    assert bm25("graph", docs, 2)[0].chunk_id == "a"
    assert bm25("quantum", docs, 2) == []


def test_rrf_deduplicates_and_rewards_agreement():
    a, b, c = [hit(x, x) for x in "abc"]
    assert [h.chunk_id for h in fuse([a, b], [c, b], k=3)] == ["b", "a", "c"]


def test_chroma_scope_and_shared_sparse_documents(tmp_path):
    store = VectorStore(tmp_path / "db")
    for pid in ("p1", "p2"):
        store.upsert_paper_chunks(pid, [f"{pid} graph"],
                                 [{"paper_id": pid, "section": "方法", "page": 2}], [[1., 0.]])
    r = Retriever(store, embed_fn=lambda _: [[1., 0.]], cfg={"retrieval": {"hybrid": True}})
    result = r.retrieve("graph", paper_id="p2")
    assert [h.chunk_id for h in result] == ["p2::0"]
    store.remove_paper("p2")
    assert r.retrieve("graph", paper_id="p2") == []


class Store:
    def all_hits(self, *, paper_id=None):
        return [hit("a", "graph attention"), hit("b", "biology")]

    def query(self, vector, *, k, paper_id=None):
        return self.all_hits()[::-1]


def test_embedding_failure_falls_back_to_sparse():
    r = Retriever(Store(), embed_fn=lambda _: (_ for _ in ()).throw(RuntimeError()), cfg={})
    assert [h.chunk_id for h in r.retrieve("attention")] == ["a"]
    assert "BM25" in r.notes[0]


def test_rerank_failure_preserves_fused_hits():
    r = Retriever(Store(), embed_fn=lambda _: [[1.]], cfg={"reranker": {"enabled": True}},
                  rerank_fn=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    assert r.retrieve("graph")
    assert "重排序" in r.notes[0]


def test_low_evidence_refuses_without_generation():
    r = Retriever(Store(), cfg={})
    hits = [replace(hit("a", "biology"), relevance_score=.01)]
    answer, used = answer_question("graph?", retriever=r, hits=hits,
        chat_fn=lambda *a, **kw: pytest.fail("低证据不应生成"))
    assert "证据相关性不足" in answer and used == []


def test_sources_keep_each_chunk_page():
    hs = [hit("p1::0", "first"), replace(hit("p1::1", "second"), page=2)]
    sources = sources_from_hits(hs)
    assert sources[0]["n"] == [1, 2]
    assert [c["page"] for c in sources[0]["chunks"]] == [1, 2]
    assert sources[0]["chunks"][1]["text"] == "second"


def test_confident_evidence_false_refusal_is_reviewed_once():
    r = Retriever(Store(), cfg={})
    calls = []
    def chat(messages, **kwargs):
        calls.append(kwargs)
        return ("论文库中的内容未提及" if len(calls) == 1 else "graph attention [1]"), {}
    answer, _ = answer_question("方法？", retriever=r,
        hits=[replace(hit("a", "graph attention"), relevance_score=.8)], chat_fn=chat)
    assert answer == "graph attention [1]"
    assert len(calls) == 2 and calls[-1]["thinking"] is True


def test_pages_do_not_leak_across_section():
    cs = split_markdown("<!-- p.1 -->\n## Methods\nfirst page\n<!-- p.2 -->\nsecond page", "p", "T")
    assert [(c.section, c.page) for c in cs] == [("Methods", 1), ("Methods", 2)]


def test_no_punctuation_still_respects_chunk_budget():
    cs = split_markdown("## Methods\n" + "字" * 2000, "p", "T", max_tokens=100)
    assert len(cs) > 10
    assert all(estimate_tokens(c.text) <= 100 for c in cs)


def test_query_rewrite_preserves_original_question():
    calls = []
    def chat(messages, **kw):
        calls.append(messages)
        return ("graph attention dataset" if len(calls) == 1 else "数据集 [1]"), {}
    r = Retriever(Store(), embed_fn=lambda texts: [[1.]], cfg={})
    answer, _ = answer_question("它用了什么数据集？", retriever=r,
                               history=[("graph attention 是什么", "一种方法")], chat_fn=chat)
    assert answer == "数据集 [1]"
    assert "它用了什么数据集？" in calls[-1][-1]["content"]


def test_reranker_validates_and_preserves_ids(monkeypatch):
    def post(url, **kwargs):
        assert url.endswith("/rerank")
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": .9}, {"index": 0, "relevance_score": .2}]},
            request=httpx.Request("POST", url))
    monkeypatch.setenv("TEST_RERANK_KEY", "test")
    monkeypatch.setattr(httpx, "post", post)
    hs = rerank("q", [hit("a", "A"), hit("b", "B")],
                {"base_url": "https://test/v1", "api_key_env": "TEST_RERANK_KEY"}, k=2)
    assert [h.chunk_id for h in hs] == ["b", "a"]
    assert hs[0].relevance_score == .9


def test_unicode_project_chroma_uses_ascii_relative_path(monkeypatch, tmp_path):
    import os
    from pathlib import Path
    import chromadb
    if os.name != "nt":
        pytest.skip("Windows HNSW path compatibility")
    project = tmp_path / "个人文献库智能体"
    project.mkdir()
    monkeypatch.chdir(project)
    captured = []
    class Client:
        def get_or_create_collection(self, *args, **kwargs):
            return object()
    monkeypatch.setattr(chromadb, "PersistentClient", lambda **kw: (captured.append(kw["path"]) or Client()))
    VectorStore(project / "data" / "db")
    assert captured == [str(Path("data") / "db")]
    assert Path(captured[0]).resolve() == project / "data" / "db"
