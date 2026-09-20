"""Europe PMC client：生物医学文献（MEDLINE/PubMed 全量镜像 + OA 全文，免 Key）。

API 文档：https://europepmc.org/RestfulWebService
- resultType=core 返回摘要与全文链接；
- PDF 取 fullTextUrlList 中 documentStyle=pdf 的条目。
"""

from __future__ import annotations

import re
import time

from paper_agent.models import Paper
from paper_agent.sources import SourceUnavailable
from paper_agent.sources._http import http_get

_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

_TAG_RE = re.compile(r"<[^>]+>")


def parse_hit(hit: dict) -> Paper:
    title = _TAG_RE.sub("", hit.get("title") or "").strip()
    pmcid = hit.get("pmcid") or ""
    if pmcid:
        source_id = f"europepmc:{pmcid}"
    else:
        source_id = f"europepmc:{hit.get('source', 'MED')}:{hit.get('id', '')}"
    urls = (hit.get("fullTextUrlList") or {}).get("fullTextUrl") or []
    pdf_urls = [u["url"] for u in urls if u.get("documentStyle") == "pdf" and u.get("url")]
    landing = next(
        (u["url"] for u in urls if u.get("documentStyle") == "html" and u.get("url")), None
    )
    pub_year = str(hit.get("pubYear") or "")
    authors = [a.strip() for a in (hit.get("authorString") or "").split(",") if a.strip()]
    return Paper(
        source="europepmc",
        source_id=source_id,
        title=title,
        authors=authors[:20],
        abstract=hit.get("abstractText") or "",
        year=int(pub_year) if pub_year.isdigit() else None,
        doi=hit.get("doi") or None,
        citations=hit.get("citedByCount"),
        pdf_urls=pdf_urls,
        abstract_only=not pdf_urls,
        landing_page=landing,
    )


def parse_response(data: dict) -> list[Paper]:
    hits = (data.get("resultList") or {}).get("result") or []
    return [p for p in (parse_hit(h) for h in hits) if p.title]


def build_query(
    query: str,
    year_from: int | None = None,
    *,
    field: str = "all",
    author: str | None = None,
    year_to: int | None = None,
    oa_only: bool = False,
) -> str:
    """构造 Europe PMC 查询串（纯函数，离线可测）。

    字段限定用其官方查询语法：TITLE:"…" / ABSTRACT:"…"；作者 AUTH:"…"；
    OA 过滤 OPEN_ACCESS:Y。保留字符清理照旧，防止用户输入改变语义。
    """
    clean = lambda text: re.sub(r"""["^\[\]{}()~*?:\\]""", " ", text).strip()  # noqa: E731
    q = clean(query)
    if field == "title":
        q = f'TITLE:"{q}"'
    elif field == "abstract":
        q = f'ABSTRACT:"{q}"'
    clauses = [f"({q})"]
    if author:
        clauses.append(f'(AUTH:"{clean(author)}")')
    lo = year_from or 1900
    hi = year_to or 2099
    if year_from or year_to:
        clauses.append(f"(PUB_YEAR:[{lo} TO {hi}])")
    if oa_only:
        clauses.append("(OPEN_ACCESS:Y)")
    return " AND ".join(clauses)


def build_params(query: str, max_results: int, *, sort: str = "relevance") -> dict:
    """构造请求参数（纯函数，离线可测）。sort：relevance（默认）/ citations / newest。"""
    params: dict = {"query": query, "format": "json", "pageSize": max_results,
                    "resultType": "core"}
    if sort == "citations":
        params["sort"] = "CITED desc"
    elif sort == "newest":
        params["sort"] = "P_PDATE_D desc"
    return params


def search(
    query: str,
    max_results: int = 10,
    year_from: int | None = None,
    *,
    proxy: str = "",
    field: str = "all",
    author: str | None = None,
    sort: str = "relevance",
    year_to: int | None = None,
    oa_only: bool = False,
) -> list[Paper]:
    safe_query = build_query(
        query, year_from, field=field, author=author, year_to=year_to, oa_only=oa_only
    )
    params = build_params(safe_query, max_results, sort=sort)
    started = time.perf_counter()
    resp = http_get(_API, params=params, proxy=proxy, timeout=(10, 60))
    _ = started
    if resp.status_code != 200:
        raise SourceUnavailable(f"Europe PMC HTTP {resp.status_code}: {resp.text[:120]}")
    return parse_response(resp.json())


def probe(proxy: str = "") -> str:
    started = time.perf_counter()
    resp = http_get(
        _API,
        params={"query": "electron", "format": "json", "pageSize": 1},
        proxy=proxy,
        timeout=(8, 20),
    )
    cost = time.perf_counter() - started
    if resp.status_code != 200:
        raise SourceUnavailable(f"HTTP {resp.status_code}")
    return f"HTTP 200，检索通道正常（{cost:.1f}s）"
