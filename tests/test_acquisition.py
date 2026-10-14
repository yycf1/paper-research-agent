"""保存后下载编排：真实临时账本，网络边界替换；不调用模型。"""
from pathlib import Path

from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.services import acquisition as acq


def test_download_only_selected_and_keep_partial_failures(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library.db")
    cfg = {"data_dir": str(tmp_path / "data")}
    for sid in ["local:success", "local:failed", "local:no-link", "local:cached", "local:unselected"]:
        lib.upsert_paper(Paper(source="local", source_id=sid, title=sid,
                               pdf_urls=[] if sid.endswith("no-link") else ["https://example.org/file.pdf"]))
    cached = tmp_path / "cached.docx"
    cached.write_bytes(b"existing fixture")
    lib.set_status("local:cached", "indexed", pdf_path=str(cached))
    calls = []

    def download(paper, *args, **kwargs):
        calls.append(paper.source_id)
        if paper.source_id == "local:failed":
            raise RuntimeError("HTTP 403 source refused")
        path = tmp_path / "downloaded.pdf"
        path.write_bytes(b"boundary fixture")
        return path

    monkeypatch.setattr(acq, "download_pdf", download)
    progress = {}
    try:
        acq.download_selected_papers(["local:success", "local:failed", "local:no-link", "local:cached", "local:success"], cfg, lib, progress)
        assert calls == ["local:success", "local:failed"]
        assert progress["completed"] == progress["total"] == 4
        assert [row["status"] for row in progress["items"]] == ["saved", "failed", "no_link", "already_saved"]
        assert lib.get("local:success").status == "downloaded"
        assert Path(lib.get("local:success").pdf_path).is_file()
        assert lib.get("local:failed").status == "download_failed"
        assert lib.get("local:no-link").status == "discovered"
        assert lib.get("local:unselected").status == "discovered"
        assert lib.get("local:cached").status == "indexed"
    finally:
        lib.close()


def test_missing_record_is_not_recreated(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library.db")
    try:
        result = {}
        acq.download_selected_papers(["local:removed"], {}, lib, result)
        assert result["items"][0]["status"] == "missing"
        assert lib.get("local:removed") is None
    finally:
        lib.close()
