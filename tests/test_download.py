"""download_pdf 落地页回退测试：HTML 响应解析 citation_pdf_url 后插队重试。"""

from __future__ import annotations

import pytest

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
    return Paper(source="openalex", source_id="openalex:W1", title="T", pdf_urls=list(urls))


def test_download_follows_citation_pdf_url(monkeypatch, tmp_path):
    """落地页 HTML → 提取 citation_pdf_url → 二跳下载真实 PDF 成功。"""
    landing = b'<html><head><meta name="citation_pdf_url" content="https://ojs.x/real.pdf"></head></html>'
    seen: list[str] = []

    def fake_get(url, **kw):
        seen.append(url)
        if url.endswith("/landing"):
            return _FakeResp(content_type="text/html; charset=utf-8", body=landing)
        return _FakeResp(content_type="application/pdf", body=b"%PDF-1.4 fake-bytes")

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
    assert not (tmp_path / "openalex_W1" / "paper.pdf").exists()


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
        lambda url, **kw: _FakeResp(content_type="application/pdf", body=b"%PDF-1.4 ok"),
    )
    out = download_pdf(_paper("https://x/real.pdf"), tmp_path)
    assert out.exists()
