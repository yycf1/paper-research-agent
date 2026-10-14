"""download_pdf 落地页回退测试：HTML 响应解析 citation_pdf_url 后插队重试。"""

from __future__ import annotations

import pymupdf
import pytest
import requests

from paper_agent import download as dl
from paper_agent.download import DownloadError, download_pdf
from paper_agent.models import Paper


class _FakeResp:
    def __init__(self, *, status=200, content_type="text/html", body=b""):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        yield self._body


def _paper(*urls: str) -> Paper:
    return Paper(source="local", source_id="local:test", title="T", pdf_urls=list(urls))


def _pdf() -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Original download test fixture")
        return doc.tobytes()


def test_download_follows_citation_pdf_url(monkeypatch, tmp_path):
    """落地页 HTML → 提取 citation_pdf_url → 二跳下载真实 PDF 成功。"""
    landing = b'<html><head><meta name="citation_pdf_url" content="https://ojs.x/real.pdf"></head></html>'
    seen: list[str] = []

    def fake_get(url, **kw):
        seen.append(url)
        if url.endswith("/landing"):
            return _FakeResp(content_type="text/html; charset=utf-8", body=landing)
        return _FakeResp(content_type="application/pdf", body=_pdf())

    monkeypatch.setattr(dl.requests, "get", fake_get)
    out = download_pdf(_paper("https://doi.org/x/landing"), tmp_path)
    assert out.exists() and out.read_bytes().startswith(b"%PDF")
    assert seen == ["https://doi.org/x/landing", "https://ojs.x/real.pdf"]  # 插队二跳


def test_landing_without_meta_fails_with_both_errors(monkeypatch, tmp_path):
    """落地页无 citation_pdf_url：记录两处错误后失败，不误存 HTML。"""
    monkeypatch.setattr(
        dl.requests, "get",
        lambda url, **kw: _FakeResp(content_type="text/html", body=b"<html>no meta</html>"),
    )
    with pytest.raises(DownloadError) as ei:
        download_pdf(_paper("https://doi.org/x/landing"), tmp_path)
    assert "非 PDF 内容" in str(ei.value)
    assert not (tmp_path / "local_test" / "paper.pdf").exists()


def test_non_html_rejects_without_retry(monkeypatch, tmp_path):
    """非 HTML 的非 PDF 响应（如图片/验证页）不触发落地页解析。"""
    seen: list[str] = []

    def fake_get(url, **kw):
        seen.append(url)
        return _FakeResp(content_type="image/png", body=b"\x89PNG fake")

    monkeypatch.setattr(dl.requests, "get", fake_get)
    with pytest.raises(DownloadError):
        download_pdf(_paper("https://x/a", "https://x/b"), tmp_path)
    assert seen == ["https://x/a", "https://x/b"]  # 只按原候选顺序，无插队


def test_real_pdf_short_circuit(monkeypatch, tmp_path):
    """首个候选就是真 PDF：直接落盘，不读后续。"""
    monkeypatch.setattr(
        dl.requests, "get",
        lambda url, **kw: _FakeResp(content_type="application/pdf", body=_pdf()),
    )
    out = download_pdf(_paper("https://x/real.pdf"), tmp_path)
    assert out.exists()


def test_relative_pdf_meta_uses_final_response_url(monkeypatch, tmp_path):
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        if len(seen) == 1:
            response = _FakeResp(body=b'<meta content="../paper.pdf?x=1&amp;y=2" name="citation_pdf_url">')
            response.url = "https://journal.example/article/view/123"
            return response
        return _FakeResp(content_type="application/pdf", body=_pdf())

    monkeypatch.setattr(dl.requests, "get", get)
    download_pdf(_paper("https://doi.org/10.example/paper"), tmp_path)
    assert seen[1] == "https://journal.example/article/paper.pdf?x=1&y=2"


def test_wrong_pdf_content_type_does_not_accept_html(monkeypatch, tmp_path):
    monkeypatch.setattr(dl.requests, "get", lambda *a, **kw: _FakeResp(
        content_type="application/pdf", body=b"<html>access denied</html>"))
    with pytest.raises(DownloadError):
        download_pdf(_paper("https://publisher.example/paper"), tmp_path)
    assert not (tmp_path / "local_test/paper.pdf").exists()


def test_connection_reset_is_retried_once(monkeypatch, tmp_path):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("Connection reset")
        return _FakeResp(content_type="application/pdf", body=_pdf())

    monkeypatch.setattr(dl.requests, "get", get)
    download_pdf(_paper("https://arxiv.org/pdf/2111.10778"), tmp_path)
    assert len(calls) == 2


def test_legacy_record_refreshes_open_alternatives_after_403(monkeypatch, tmp_path):
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        return _FakeResp(status=403) if "publisher" in url else _FakeResp(content_type="application/pdf", body=_pdf())

    monkeypatch.setattr(dl.requests, "get", get)
    monkeypatch.setattr(dl, "_refresh_candidates", lambda paper, proxy: ["https://arxiv.org/pdf/2111.10778"], raising=False)
    download_pdf(_paper("https://publisher.example/paper.pdf"), tmp_path)
    assert seen == ["https://publisher.example/paper.pdf", "https://arxiv.org/pdf/2111.10778"]


def test_interrupted_body_cleans_partial_and_preserves_old_file(monkeypatch, tmp_path):
    dest = tmp_path / "local_test/paper.pdf"
    dest.parent.mkdir()
    old = _pdf()
    dest.write_bytes(old)

    class Broken(_FakeResp):
        def iter_content(self, chunk_size):
            yield _pdf()[:128]
            raise requests.ConnectionError("interrupted body")

    monkeypatch.setattr(dl.requests, "get", lambda *a, **kw: Broken(content_type="application/pdf"))
    with pytest.raises(DownloadError):
        download_pdf(_paper("https://example.org/paper.pdf"), tmp_path)
    assert dest.read_bytes() == old
    assert not list(dest.parent.glob("*.part"))


def test_truncated_pdf_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(dl.requests, "get", lambda *a, **kw: _FakeResp(content_type="application/pdf", body=b"%PDF-1.4\ntruncated"))
    with pytest.raises(DownloadError):
        download_pdf(_paper("https://example.org/paper.pdf"), tmp_path)
    assert not (tmp_path / "local_test/paper.pdf").exists()


@pytest.mark.parametrize("status,wait,expected", [(503, "0", 2), (429, "1", 2), (429, "60", 1), (403, "0", 1)])
def test_status_retry_is_bounded(monkeypatch, tmp_path, status, wait, expected):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = _FakeResp(status=status)
        response.headers["Retry-After"] = wait
        return response

    monkeypatch.setattr(dl.requests, "get", get)
    monkeypatch.setattr(dl.time, "sleep", lambda *a: None)
    with pytest.raises(DownloadError):
        download_pdf(_paper("https://example.org/paper.pdf"), tmp_path)
    assert len(calls) == expected


def test_html_chain_has_request_limit(monkeypatch, tmp_path):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return _FakeResp(body=f'<meta name="citation_pdf_url" content="https://example.org/{len(calls)}">'.encode())

    monkeypatch.setattr(dl.requests, "get", get)
    with pytest.raises(DownloadError, match="12 次请求上限"):
        download_pdf(_paper("https://example.org/start"), tmp_path)
    assert len(calls) == 12
