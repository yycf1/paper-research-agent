"""Acceptance regressions: isolated ledgers/files, no model or public network calls."""
import time
from dataclasses import replace

import pymupdf
import pytest
from fastapi.testclient import TestClient

from paper_agent import paths
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.server import jobs, routes
from paper_agent.server.app import create_app
from paper_agent.services import library_ops, search


def pdf_bytes():
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Original acceptance fixture")
        return doc.tobytes()


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = {"data_dir": str(tmp_path), "retrieval": {"mode": "bm25"}}
    monkeypatch.setattr(routes, "load_config", lambda: cfg)
    lib = paths.library(cfg)
    client = TestClient(create_app())
    yield cfg, lib, client
    lib.close()


def test_cross_source_ingest_merges_into_existing_id(env):
    cfg, lib, _ = env
    paper = Paper(source="openalex", source_id="openalex:W123", title="Same paper", doi="10.123/a")
    lib.upsert_paper(paper)
    candidate = replace(paper, source="arxiv", source_id="arxiv:2401.12345", pdf_urls=["https://arxiv.org/pdf/2401.12345"])
    outcome = search.ingest_papers([candidate], lib=lib)
    assert len(lib.list()) == 1
    assert lib.get(paper.source_id).paper.pdf_urls == candidate.pdf_urls
    assert outcome.selected_ids == [paper.source_id]


def test_slash_group_rename_and_delete(env):
    _, _, client = env
    assert client.post("/api/tags", json={"name": "CV/影像"}).status_code == 201
    assert client.patch("/api/tags/CV%2F影像", json={"name": "医学 / 新组"}).status_code == 200
    assert client.delete("/api/tags/医学%20%2F%20新组").status_code == 200


def test_invalid_upload_rejected_before_ledger_write(env):
    _, lib, client = env
    reply = client.post("/api/papers/upload", files={"file": ("fake.pdf", b"not a PDF", "application/pdf")})
    assert reply.status_code == 400
    assert lib.list() == []


def test_attach_fulltext_preserves_original_record_and_opens_pdf(env):
    _, lib, client = env
    lib.upsert_paper(Paper(source="openalex", source_id="openalex:W123", title="Original title"), tag="研究")
    reply = client.post("/api/papers/openalex:W123/fulltext", files={"file": ("paper.pdf", pdf_bytes(), "application/pdf")})
    assert reply.status_code == 200
    assert len(lib.list()) == 1
    rec = lib.get("openalex:W123")
    assert rec.paper.title == "Original title" and rec.tag == "研究"
    assert rec.status == "downloaded"
    original = client.get("/api/papers/openalex:W123/file")
    assert original.status_code == 200 and original.content.startswith(b"%PDF-")
    assert client.post("/api/papers/openalex:W123/fulltext", files={"file": ("x.pdf", pdf_bytes())}).status_code == 409


def test_delete_failure_is_visible_recoverable_and_excluded_from_retrieval(env, monkeypatch):
    cfg, lib, client = env
    from paper_agent.rag.qa import Retriever
    from paper_agent.rag.vectorstore import VectorStore
    paper = Paper(source="local", source_id="local:delete-me", title="Removed paper")
    lib.upsert_paper(paper)
    store = VectorStore(paths.vector_dir(cfg))
    store.upsert_paper_chunks(paper.source_id, ["ghost result"],
                             [{"paper_id": paper.source_id, "source_sha256": "stale", "mode": "fulltext"}], [[1., 0.]])
    lib.upsert_paper(Paper(source="local", source_id="local:keep", title="Keep paper"))
    store.upsert_paper_chunks("local:keep", ["valid result"], [{"paper_id": "local:keep"}], [[0., 1.]])
    remove = VectorStore.remove_paper
    monkeypatch.setattr(VectorStore, "remove_paper", lambda *_: (_ for _ in ()).throw(RuntimeError("controlled outage")))
    reply = client.delete("/api/papers/local:delete-me")
    assert reply.status_code == 202 and reply.json()["cleanup_pending"] is True
    assert reply.json()["ok"] is False and lib.get(paper.source_id) is None
    assert [h.paper_id for h in Retriever(store, cfg=cfg).retrieve("result")] == ["local:keep"]
    from paper_agent.rag.qa import answer_question
    monkeypatch.setattr(routes, "answer_question", lambda question, **kwargs: answer_question(
        question, **kwargs, chat_fn=lambda *a, **kw: ("valid result [1]", {})))
    answer = client.post("/api/ask", json={"question": "result"})
    assert answer.status_code == 200
    assert [source["paper_id"] for source in answer.json()["sources"]] == ["local:keep"]
    monkeypatch.setattr(VectorStore, "remove_paper", remove)
    reply = client.post("/api/maintenance/cleanup")
    assert reply.status_code == 200 and reply.json()["remaining"] == 0
    assert store.count() == 1


def test_jobs_survive_restart_and_active_rows_become_interrupted(tmp_path):
    registry = jobs.JobRegistry(tmp_path / "jobs.db")
    job = registry.submit("download_papers", lambda result: result.update(items=[{"source_id": "local:a", "status": "failed"}]),
                          result={"source_ids": ["local:a"]})
    deadline = time.monotonic() + 5
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(.01)
    assert job.status == "done"
    restored = jobs.JobRegistry(tmp_path / "jobs.db")
    assert restored.get(job.id).result["items"][0]["status"] == "failed"
    active = jobs.Job(id="interrupted", kind="download_papers", result={"items": [{"source_id": "local:b", "status": "running"}]})
    registry.save(active)
    restored = jobs.JobRegistry(tmp_path / "jobs.db")
    assert restored.get("interrupted").status == "interrupted"
    assert restored.get("interrupted").result["items"][0]["status"] == "interrupted"


def wait_job(client, job):
    for _ in range(200):
        job = client.get("/api/jobs/" + job["id"]).json()
        if job["status"] != "running":
            return job
        time.sleep(.01)
    raise AssertionError("job did not terminate")


def test_retry_only_unsuccessful_downloads_and_never_analyzes(env, monkeypatch):
    cfg, lib, client = env
    from paper_agent.services import acquisition
    from paper_agent.download import DownloadError
    for sid in ("local:good", "local:retry", "local:untouched"):
        lib.upsert_paper(Paper(source="local", source_id=sid, title=sid, pdf_urls=["https://fixture.example/pdf"]))
    calls = []

    def download(paper, directory, **kwargs):
        calls.append(paper.source_id)
        if paper.source_id == "local:retry" and calls.count(paper.source_id) == 1:
            raise DownloadError("source HTTP 403", code="access_denied")
        target = directory / paper.source_id.replace(":", "_") / "paper.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pdf_bytes())
        return target

    monkeypatch.setattr(acquisition, "download_pdf", download)
    monkeypatch.setattr(routes, "process_paper", lambda *_: pytest.fail("analysis must not run"))
    initial = wait_job(client, client.post("/api/papers/download", json={"source_ids": ["local:good", "local:retry"]}).json())
    assert [i["status"] for i in initial["result"]["items"]] == ["saved", "failed"]
    assert initial["result"]["items"][1]["failure_kind"] == "access_denied"
    retry = wait_job(client, client.post(f"/api/jobs/{initial['id']}/retry").json())
    assert retry["result"]["source_ids"] == ["local:retry"]
    assert calls == ["local:good", "local:retry", "local:retry"]
    assert lib.get("local:untouched").status == "discovered"
    assert client.post(f"/api/jobs/{retry['id']}/retry").status_code == 409


def test_blank_pdf_upload_is_actionable(env):
    _, lib, client = env
    with pymupdf.open() as doc:
        doc.new_page()
        blank = doc.tobytes()
    reply = client.post("/api/papers/upload", files={"file": ("blank.pdf", blank)})
    assert reply.status_code == 400
    assert "空白" in reply.json()["detail"]
    assert lib.list() == []


def test_corrupt_cached_file_is_not_reported_as_saved(env, monkeypatch):
    cfg, lib, client = env
    from paper_agent.services import acquisition
    sid = "local:bad-cache"
    path = paths.papers_dir(cfg) / "local_bad-cache/paper.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"HTML access denied")
    lib.upsert_paper(Paper(source="local", source_id=sid, title="Bad cache", pdf_urls=["https://fixture.example/pdf"]))
    lib.set_status(sid, "downloaded", pdf_path=str(path))
    def download(*args, **kwargs):
        path.write_bytes(pdf_bytes())
        return path
    monkeypatch.setattr(acquisition, "download_pdf", download)
    job = wait_job(client, client.post("/api/papers/download", json={"source_ids": [sid]}).json())
    assert job["result"]["items"][0]["status"] == "saved"
    assert path.read_bytes().startswith(b"%PDF-")


def test_original_file_cannot_serve_unmanaged_path(env, tmp_path):
    _, lib, client = env
    path = tmp_path / "outside.pdf"
    path.write_bytes(pdf_bytes())
    lib.upsert_paper(Paper(source="local", source_id="local:outside", title="Outside"))
    lib.set_status("local:outside", "downloaded", pdf_path=str(path))
    assert client.get("/api/papers/local:outside/file").status_code == 403


def test_human_review_persists_and_rejects_changed_evidence(env):
    cfg, lib, client = env
    import json
    from paper_agent.rag.vectorstore import VectorStore
    sid = "local:review"
    lib.upsert_paper(Paper(source="local", source_id=sid, title="Review"))
    directory = paths.knowledge_dir(cfg) / "local_review"
    directory.mkdir(parents=True)
    (directory / "experiment.json").write_text(json.dumps({"datasets": [{"name": "ORIGA"}]}), encoding="utf-8")
    store = VectorStore(paths.vector_dir(cfg))
    store.upsert_paper_chunks(sid, ["Dataset ORIGA is used."], [{"paper_id": sid}], [[1., 0.]])
    data = client.get(f"/api/papers/{sid}/facts").json()
    fact = data["fields"]["datasets"][0]
    payload = {"source_version": data["source_version"], "claim_sha256": fact["claim_sha256"],
               "reviewer": "tester", "review_kind": "human", "status": "supported", "note": "Confirmed in original text"}
    reply = client.post(f"/api/papers/{sid}/facts/reviews", json=payload)
    assert reply.status_code == 200
    assert client.get(f"/api/papers/{sid}/facts").json()["fields"]["datasets"][0]["support_status"] == "supported"
    store.upsert_paper_chunks(sid, ["Dataset ORIGA with changed evidence."], [{"paper_id": sid}], [[1., 0.]])
    assert client.post(f"/api/papers/{sid}/facts/reviews", json=payload).status_code == 409
    assert client.get(f"/api/papers/{sid}/facts").json()["fields"]["datasets"][0]["support_status"] == "unverified"


def test_openalex_key_is_used_for_search_refresh_and_probe(monkeypatch):
    from paper_agent.sources import openalex
    from types import SimpleNamespace
    seen = []
    def get(url, **kwargs):
        seen.append((url, kwargs["params"]))
        return SimpleNamespace(status_code=200, json=lambda: {"results": [], "id": "https://openalex.org/W123"})
    monkeypatch.setenv("OPENALEX_API_KEY", "private-test-key")
    monkeypatch.setattr(openalex, "http_get", get)
    openalex.search("test")
    openalex.refresh_pdf_urls("openalex:W123")
    openalex.probe()
    openalex.refresh_pdf_urls("europepmc:PMC123", doi="10.1234/example")
    assert all(params["api_key"] == "private-test-key" for _, params in seen)
    assert "doi.org/10.1234/example" in seen[-1][0]


def test_openalex_budget_error_is_not_network_failure_and_hides_key(monkeypatch):
    from paper_agent.sources import openalex, SourceUnavailable
    from types import SimpleNamespace
    monkeypatch.setenv("OPENALEX_API_KEY", "private-test-key")
    response = SimpleNamespace(status_code=429, text="Daily budget exhausted private-test-key", headers={"X-RateLimit-Remaining": "0"})
    monkeypatch.setattr(openalex, "http_get", lambda *a, **kw: response)
    with pytest.raises(SourceUnavailable) as error:
        openalex.search("test")
    assert "预算" in str(error.value) and "private-test-key" not in str(error.value)


def test_replace_fulltext_invalidates_old_index_and_analysis(env):
    cfg, lib, client = env
    from paper_agent.rag.vectorstore import VectorStore
    sid = "local:replace"
    lib.upsert_paper(Paper(source="local", source_id=sid, title="Preserved title"))
    initial = client.post(f"/api/papers/{sid}/fulltext", files={"file": ("first.pdf", pdf_bytes())})
    assert initial.status_code == 200
    store = VectorStore(paths.vector_dir(cfg))
    store.upsert_paper_chunks(sid, ["old text"], [{"paper_id": sid}], [[1., 0.]])
    parsed = paths.parsed_dir(cfg) / "local_replace"
    parsed.mkdir(parents=True)
    (parsed / "full_text.md").write_text("old parsed text", encoding="utf-8")
    knowledge = paths.knowledge_dir(cfg) / "local_replace"
    knowledge.mkdir(parents=True)
    (knowledge / "report.md").write_text("old report", encoding="utf-8")
    bad = client.post(f"/api/papers/{sid}/fulltext", data={"replace_existing": "true"}, files={"file": ("bad.pdf", b"invalid")})
    assert bad.status_code == 400 and store.count() == 1 and parsed.exists()
    reply = client.post(f"/api/papers/{sid}/fulltext", data={"replace_existing": "true"}, files={"file": ("new.pdf", pdf_bytes())})
    assert reply.status_code == 200
    assert store.count() == 0 and not parsed.exists() and not knowledge.exists()
    assert lib.get(sid).status == "downloaded" and lib.get(sid).paper.title == "Preserved title"


def test_empty_openalex_links_refresh_metadata_once(tmp_path, monkeypatch):
    from paper_agent import download
    paper = Paper(source="openalex", source_id="openalex:W123", title="Refresh")
    seen = []
    monkeypatch.setattr(download, "_refresh_candidates", lambda p, proxy: seen.append(p.source_id) or ["https://fixture.example/pdf"])
    class Response:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, **kwargs): yield pdf_bytes()
    monkeypatch.setattr(download.requests, "get", lambda *a, **kw: Response())
    assert download.download_pdf(paper, tmp_path).read_bytes().startswith(b"%PDF-")
    assert seen == [paper.source_id]


def test_doi_landing_can_download_without_metadata_api(tmp_path, monkeypatch):
    from paper_agent import download
    paper = Paper(source="europepmc", source_id="europepmc:PMC123", title="DOI fallback",
                  doi="10.1234/example", pdf_urls=["https://fixture.example/restricted"])
    seen = []
    class Response:
        def __init__(self, url):
            self.url = url
            self.status_code = 403 if url.endswith("restricted") else 200
            self.headers = {"content-type": "application/pdf" if url.endswith("real.pdf") else "text/html"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, **kwargs):
            yield pdf_bytes() if self.url.endswith("real.pdf") else b'<meta name="citation_pdf_url" content="https://fixture.example/real.pdf">'
    def get(url, **kwargs):
        seen.append(url)
        return Response(url)
    monkeypatch.setattr(download.requests, "get", get)
    monkeypatch.setattr(download, "_refresh_candidates", lambda *a: pytest.fail("metadata API should not be needed"))
    assert download.download_pdf(paper, tmp_path).exists()
    assert seen == ["https://fixture.example/restricted", "https://doi.org/10.1234/example", "https://fixture.example/real.pdf"]
