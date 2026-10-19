"""候选全文下载：开放副本回退、有界重试、落地页解析与真实 PDF 校验。"""

from __future__ import annotations

import time
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import pymupdf
import requests

from paper_agent.models import Paper
from paper_agent.sources import SourceUnavailable
from paper_agent.sources.openalex import refresh_pdf_urls

_UA = "paper-agent/0.1 (personal research reading tool)"
_LANDING_SCAN_BYTES = 256 * 1024
_MAX_REQUESTS = 12  # 包括落地页和重试，另允许一次 OpenAlex 元数据查询。
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_RETRY_DELAY = 3


class DownloadError(Exception):
    """没有可下载/可校验的全文；错误保留来源拒绝、网络与内容原因。"""

    def __init__(self, message: str, *, code: str = "download_failed", attempts: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.attempts = attempts or []


def safe_dirname(source_id: str) -> str:
    return source_id.replace(":", "_").replace("/", "_")


class _PdfMeta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        data = {name.lower(): value for name, value in attrs}
        name = (data.get("name") or data.get("property") or "").lower()
        if tag == "meta" and name == "citation_pdf_url" and data.get("content"):
            self.urls.append(data["content"])
        if tag == "link" and (data.get("type") or "").lower() == "application/pdf" and data.get("href"):
            self.urls.append(data["href"])


def _citation_pdf_url(head: bytes, chunks) -> str | None:
    buf = head[:_LANDING_SCAN_BYTES]
    parser = _PdfMeta()
    parser.feed(buf.decode("utf-8", errors="replace"))
    if parser.urls:
        return parser.urls[0]
    for chunk in chunks:
        if len(buf) >= _LANDING_SCAN_BYTES:
            break
        buf += chunk[:_LANDING_SCAN_BYTES - len(buf)]
        parser = _PdfMeta()
        parser.feed(buf.decode("utf-8", errors="replace"))
        if parser.urls:
            return parser.urls[0]
    return parser.urls[0] if parser.urls else None


def _refresh_candidates(paper: Paper, proxy: str) -> list[str]:
    arxiv = re.fullmatch(r"arxiv:(\d{4}\.\d{4,5}(?:v\d+)?|[a-zA-Z.-]+/\d{7}(?:v\d+)?)", paper.source_id)
    if arxiv:
        return [f"https://arxiv.org/pdf/{arxiv[1]}"]
    return refresh_pdf_urls(paper.source_id, proxy=proxy, doi=paper.doi)


def _display_url(url: str) -> str:
    """错误信息不携带下载地址的查询凭据。"""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def _validate_pdf(path: Path) -> None:
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass or doc.page_count == 0:
                raise ValueError("PDF 需要密码或没有可读页面")
            doc.load_page(0)
    except Exception as exc:
        raise ValueError(f"PDF 完整性校验失败：{exc}") from exc


def download_pdf(paper: Paper, papers_dir: Path, *, proxy: str = "") -> Path:
    """最多 12 次候选/重试请求；通过文件校验后原子替换，失败不覆盖旧文件。

    403/404 不对同一地址重试。连接错误、429/5xx 至多重试一次；429 的
    Retry-After 超过 3 秒时直接换候选。旧 OpenAlex 候选用尽后查询一次
    同 Work 的开放副本，不解验证码或绕过访问限制。
    """
    root = papers_dir.resolve()
    dest_dir = (root / safe_dirname(paper.source_id)).resolve()
    if dest_dir == root or not dest_dir.is_relative_to(root):
        raise DownloadError("论文文件目录超出文献目录")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "paper.pdf"
    tmp = dest_dir / f"paper.{uuid.uuid4().hex}.pdf.part"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    errors: list[str] = []
    reasons: list[str] = []
    tried: set[str] = set()
    queue = list(paper.pdf_urls)
    # A DOI or the article's own landing page can expose citation_pdf_url without
    # requiring a metadata API account. Never guess arbitrary other-paper links.
    if paper.landing_page:
        queue.append(paper.landing_page)
    if paper.doi and re.fullmatch(r"10\.\d{4,9}/\S+", paper.doi):
        queue.append("https://doi.org/" + quote(paper.doi, safe="/"))
    queue = list(dict.fromkeys(queue))
    refreshed = False
    requests_used = 0
    while requests_used < _MAX_REQUESTS:
        if not queue:
            if refreshed:
                break
            refreshed = True
            try:
                queue.extend(url for url in _refresh_candidates(paper, proxy) if url not in tried)
            except (SourceUnavailable, requests.RequestException, ValueError) as exc:
                errors.append(f"开放副本查询失败：{exc}")
                reasons.append("metadata_unavailable")
            if not queue:
                break
        url = queue.pop(0)
        if url in tried:
            continue
        tried.add(url)
        if urlsplit(url).scheme not in {"http", "https"}:
            errors.append("忽略非 HTTP 全文地址")
            reasons.append("invalid_url")
            continue
        for attempt in range(2):
            if requests_used >= _MAX_REQUESTS:
                break
            requests_used += 1
            retry = False
            try:
                with requests.get(url, stream=True, timeout=(10, 30), proxies=proxies,
                                  headers={"User-Agent": _UA}) as resp:
                    if resp.status_code != 200:
                        hint = "（来源拒绝访问，可尝试其他开放副本或手动导入）" if resp.status_code in {401, 403} else ""
                        errors.append(f"{_display_url(url)} -> HTTP {resp.status_code}{hint}")
                        reasons.append({401: "authorization", 403: "access_denied", 404: "not_found", 429: "rate_limited"}.get(resp.status_code, "source_error"))
                        retry_after = resp.headers.get("Retry-After", "0")
                        short_wait = retry_after.isdigit() and int(retry_after) <= _RETRY_DELAY
                        retry = attempt == 0 and (resp.status_code in {500, 502, 503, 504} or (resp.status_code == 429 and short_wait))
                    else:
                        chunks = resp.iter_content(chunk_size=65536)
                        head = next(chunks, b"")
                        content_type = resp.headers.get("content-type", "").lower()
                        if not head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"%PDF-"):
                            errors.append(f"{_display_url(url)} -> 非 PDF 内容（{content_type or '未知类型'}）")
                            reasons.append("non_pdf")
                            if "html" in content_type or head.lstrip().startswith(b"<"):
                                landed = _citation_pdf_url(head, chunks)
                                if landed:
                                    candidate = urljoin(getattr(resp, "url", url), landed)
                                    if candidate not in tried:
                                        queue.insert(0, candidate)
                            break
                        total = len(head)
                        with tmp.open("wb") as output:
                            output.write(head)
                            for chunk in chunks:
                                total += len(chunk)
                                if total > _MAX_DOWNLOAD_BYTES:
                                    raise ValueError("全文超过 100 MiB 下载限制，请手动导入")
                                output.write(chunk)
                        _validate_pdf(tmp)
                        tmp.replace(out)
                        return out
            except requests.RequestException as exc:
                errors.append(f"{_display_url(url)} -> 网络异常（{type(exc).__name__}）")
                reasons.append("network")
                retry = attempt == 0
            except ValueError as exc:
                errors.append(f"{_display_url(url)} -> {exc}")
                reasons.append("invalid_pdf")
            except OSError as exc:
                raise DownloadError(f"本地文件写入失败：{exc}", code="local_io", attempts=errors) from exc
            finally:
                # 唯一临时文件在已验证的文献子目录内；只清理本次请求创建的文件。
                tmp.unlink(missing_ok=True)
            if not retry or requests_used >= _MAX_REQUESTS:
                break
            time.sleep(_RETRY_DELAY)
    if requests_used >= _MAX_REQUESTS:
        errors.append("已达单篇 12 次请求上限")
    code = reasons[-1] if reasons else "no_link"
    # An unavailable metadata refresh must not hide the actual download rejection.
    if code == "metadata_unavailable" and len(reasons) > 1:
        code = reasons[-2]
    raise DownloadError("; ".join(errors) or "无候选全文链接；当前只有文献信息，可给此记录上传 PDF/DOCX",
                        code=code, attempts=errors)
