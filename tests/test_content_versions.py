from __future__ import annotations

import json
import os

import pytest

from paper_agent import paths
from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.rag.qa import Retriever, answer_question
from paper_agent.rag.splitter import split_markdown
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.services import process as proc
from paper_agent.services.versions import (
    EXPERIMENT_CONTRACT,
    REPORT_CONTRACT,
    artifact_current,
    index_current,
    index_metadata,
    save_artifact,
)


def test_artifact_uses_content_contract_not_mtime(tmp_path):
    artifact = tmp_path / "experiment.json"
    cfg = {"llm": {"model": "fake", "runtime_api_key": "not-a-real-key"}}
    save_artifact(artifact, "{}", "original", cfg, EXPERIMENT_CONTRACT)
    assert artifact_current(artifact, "original", cfg, EXPERIMENT_CONTRACT)
    assert not artifact_current(artifact, "changed", cfg, EXPERIMENT_CONTRACT)
    assert not artifact_current(artifact, "original", cfg, "new-contract")
    assert not artifact_current(artifact, "original", {"llm": {"model": "new"}}, EXPERIMENT_CONTRACT)
    assert artifact_current(artifact, "original", {"llm": {"model": "fake", "runtime_api_key": "rotated"}}, EXPERIMENT_CONTRACT)
    manifest = artifact.with_name(artifact.name + ".manifest.json").read_text(encoding="utf-8")
    assert "not-a-real-key" not in manifest and "runtime_api_key" not in manifest
    previous = artifact.stat().st_mtime
    artifact.write_text('{"tampered":true}', encoding="utf-8")
    os.utime(artifact, (previous, previous))
    assert not artifact_current(artifact, "original", cfg, EXPERIMENT_CONTRACT)


def _index(store, text, cfg, *, sid="p1"):
    chunks = split_markdown(text, sid, "T")
    metas = index_metadata(text, chunks, cfg, "fulltext")
    store.upsert_paper_chunks(sid, [c.text for c in chunks], metas, [[1.0, 0.0] for c in chunks])
    return metas


def test_index_fingerprint_covers_source_chunks_and_embedding(tmp_path, monkeypatch):
    store = VectorStore(tmp_path / "db")
    cfg = {"embedding": {"model": "fake"}}
    text = "## Method\ngraph attention"
    metas = _index(store, text, cfg)
    assert index_current(store, "p1", metas)
    for changed, options in [(text + " new", cfg), (text, {"embedding": {"model": "different"}})]:
        assert not index_current(store, "p1", index_metadata(changed, split_markdown(changed, "p1", "T"), options, "fulltext"))
    monkeypatch.setattr("paper_agent.rag.splitter.MAX_TOKENS", 700)
    assert not index_current(store, "p1", index_metadata(text, split_markdown(text, "p1", "T"), cfg, "fulltext"))


def test_partial_write_failure_restores_old_chunks(tmp_path, monkeypatch):
    store = VectorStore(tmp_path / "db")
    _index(store, "## Method\nold evidence", {})
    before = store.all_hits(paper_id="p1")
    original = store.collection.upsert
    calls = []

    def interrupted(**kwargs):
        calls.append(1)
        original(**kwargs)
        if len(calls) == 1:
            raise RuntimeError("injected failure after write")

    monkeypatch.setattr(store.collection, "upsert", interrupted)
    with pytest.raises(RuntimeError, match="injected"):
        _index(store, "## Method\nnew evidence\n\n## Results\nsecond chunk", {})
    assert store.all_hits(paper_id="p1") == before
    assert "old evidence" in store.query([1.0, 0.0], paper_id="p1")[0].text


@pytest.mark.parametrize("vectors", [[[1.0]], [[float("nan"), 0.0]], []])
def test_bad_vectors_preserve_old_index(tmp_path, vectors):
    store = VectorStore(tmp_path / "db")
    _index(store, "## Method\nold evidence", {})
    before = store.all_hits(paper_id="p1")
    with pytest.raises(Exception):
        store.upsert_paper_chunks("p1", ["new"], [{"paper_id": "p1"}], vectors)
    assert store.all_hits(paper_id="p1") == before


def test_stale_source_and_embedding_stop_generation(tmp_path):
    cfg = {"data_dir": str(tmp_path), "embedding": {"model": "fake"}}
    store = VectorStore(tmp_path / "db")
    _index(store, "## Method\ngraph original", cfg)
    path = paths.parsed_dir(cfg) / safe_dirname("p1") / "full_text.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Method\ngraph changed", encoding="utf-8")
    retriever = Retriever(store, cfg={**cfg, "retrieval": {"mode": "bm25"}})
    with pytest.raises(RuntimeError, match="解析文本不一致"):
        answer_question("graph", retriever=retriever, chat_fn=lambda *a, **kw: pytest.fail("不可调用模型"))
    other = Retriever(store, cfg={"embedding": {"model": "other"}},
                      embed_fn=lambda *a: pytest.fail("不兼容索引不可调用嵌入服务"))
    with pytest.raises(RuntimeError, match="嵌入配置"):
        other.retrieve("graph")


def test_process_replaces_old_fulltext_and_skips_only_current(tmp_path, monkeypatch):
    cfg = {"data_dir": str(tmp_path), "embedding": {"model": "fake"}}
    sid = "openalex:W1"
    lib = Library(tmp_path / "library.db")
    lib.upsert_paper(Paper(source="openalex", source_id=sid, title="T"))
    lib.set_status(sid, "analyzed")
    md = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("## Method\nnew evidence", encoding="utf-8")
    kd = paths.knowledge_dir(cfg) / safe_dirname(sid)
    save_artifact(kd / "experiment.json", "{}", md.read_text(encoding="utf-8"), cfg, EXPERIMENT_CONTRACT)
    save_artifact(kd / "report.md", "report", md.read_text(encoding="utf-8"), cfg, REPORT_CONTRACT)
    store = VectorStore(paths.vector_dir(cfg))
    _index(store, "## Method\nold evidence", cfg, sid=sid)
    calls = []

    def embed(texts, **kwargs):
        calls.append(texts)
        assert kwargs["cfg"] == cfg
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(proc, "embed", embed)
    monkeypatch.setattr(proc, "chat", lambda *a, **kw: pytest.fail("当前知识产物不可重做"))
    try:
        first = proc.process_paper(sid, cfg, lib)
        assert first["final_status"] == "indexed" and len(calls) == 1
        updated = store.all_hits(paper_id=sid)
        assert len(updated) == 1 and updated[0].text.endswith("new evidence")
        assert "old evidence" not in updated[0].text
        second = proc.process_paper(sid, cfg, lib)
        assert second["stages"][-1]["status"] == "skipped" and len(calls) == 1
        md.write_text("## Method\nchanged again", encoding="utf-8")
        monkeypatch.setattr(proc, "extract_experiment", lambda *a, **kw: {})
        monkeypatch.setattr(proc, "generate_report", lambda *a, **kw: ("new report", {}))
        monkeypatch.setattr(proc, "embed", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("embedding offline")))
        failed = proc.process_paper(sid, cfg, lib)
        assert failed["final_status"] == "analyzed" and failed["stages"][-1]["status"] == "failed"
        assert store.all_hits(paper_id=sid) == updated
        assert json.loads((kd / "experiment.json.manifest.json").read_text())["contract"] == EXPERIMENT_CONTRACT
    finally:
        lib.close()
