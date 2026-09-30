"""OA PDF 下载：多候选地址依次尝试，HTML 落地页自动跟随 citation_pdf_url，%PDF 魔数校验。

检索源给的「全文链接」并不总是真 PDF——OpenAlex 等常把 DOI 落地页（HTML 文章页）
存成 pdf_url。落地页通常带标准元数据 <meta name="citation_pdf_url">（OJS 平台与
多数出版社都有），解析出真实 PDF 地址插回候选队列重试一次。
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

from paper_agent.models import Paper

_UA = "paper-agent/0.1 (personal research reading tool)"

# citation_pdf_url 的两种属性顺序都兼容
_CITATION_PDF_RES = [
    re.compile(
        r"<meta[^>]+name=[\"']citation_pdf_url[\"'][^>]+content=[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    ),
    re.compile(
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']citation_pdf_url[\"']",
        re.IGNORECASE,
    ),
]
_LANDING_SCAN_BYTES = 256 * 1024  # 落地页元数据都在页首，扫描上限防爆内存


class DownloadError(Exception):
    """所有候选地址均下载失败。"""


def safe_dirname(source_id: str) -> str:
    """source_id 转目录名：Windows 不允许 ':'，统一替换为 '_'。"""
    return source_id.replace(":", "_").replace("/", "_")


def _citation_pdf_url(head: bytes, chunks) -> str | None:
    """从 HTML 落地页提取 citation_pdf_url；无则 None。"""
    buf = head
    for chunk in chunks:
        buf += chunk
        if len(buf) > _LANDING_SCAN_BYTES:
            break
    text = buf.decode("utf-8", errors="replace")
    for pattern in _CITATION_PDF_RES:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def download_pdf(paper: Paper, papers_dir: Path, *, proxy: str = "") -> Path:
    """依次尝试 paper.pdf_urls；内容校验通过才落盘。全部失败抛 DownloadError。

    校验规则：前 64KB 含 %PDF 魔数，或 Content-Type 声明为 pdf——
    防止把 HTML 落地页/验证页误存成 .pdf。HTML 响应会尝试解析
    citation_pdf_url 元数据并插队重试（落地页回退）。
    """
    if not paper.pdf_urls:
        raise DownloadError("无可用的 PDF 链接（仅摘要论文）")
    dest_dir = papers_dir / safe_dirname(paper.source_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "paper.pdf"
    tmp = dest_dir / "paper.pdf.part"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    errors: list[str] = []

    tried: set[str] = set()
    queue = list(paper.pdf_urls)
    while queue:
        url = queue.pop(0)
        if url in tried:
            continue
        tried.add(url)
        try:
            with requests.get(
                url,
                stream=True,
                timeout=(10, 120),
                proxies=proxies,
                headers={"User-Agent": _UA},
            ) as resp:
                if resp.status_code != 200:
                    errors.append(f"{url} -> HTTP {resp.status_code}")
                    continue
                chunks = resp.iter_content(chunk_size=65536)
                head = next(chunks, b"")
                content_type = resp.headers.get("content-type", "").lower()
                if b"%PDF" not in head and "pdf" not in content_type:
                    errors.append(f"{url} -> 非 PDF 内容（{content_type or '未知类型'}）")
                    if "html" in content_type:
                        landed = _citation_pdf_url(head, chunks)
                        if landed and landed not in tried:
                            queue.insert(0, landed)  # 真实 PDF 插队，保持候选优先级
                    continue
                with tmp.open("wb") as f:
                    f.write(head)
                    for chunk in chunks:
                        f.write(chunk)
                tmp.replace(out)
                return out
        except requests.RequestException as exc:
            errors.append(f"{url} -> {exc}")
            continue
    raise DownloadError("; ".join(errors))
