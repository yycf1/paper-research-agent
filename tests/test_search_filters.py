"""高级检索条件（M13）：三个数据源的过滤参数构造与服务层后过滤，纯函数离线测试。"""

from paper_agent.tools import ToolResult
from paper_agent.sources import arxiv as arxiv_src
from paper_agent.sources import europepmc as epmc_src
from paper_agent.sources import openalex as oa_src


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def test_openalex_default_all_field():
    params = oa_src.build_params("graph neural", 10)
    assert params["search"] == "graph neural"
    assert "filter" not in params
    assert "sort" not in params  # 相关度 = search 默认排序


def test_openalex_title_scope_uses_filter_and_citation_sort():
    params = oa_src.build_params("graph neural", 10, field="title")
    assert "search" not in params
    assert params["filter"] == "title.search:graph neural"
    # 字段限定下无相关度分值，退化按引用数
    assert params["sort"] == "cited_by_count:desc"


def test_openalex_full_conditions():
    params = oa_src.build_params(
        "segmentation", 20, 2020, field="abstract", author="Zhou, B.",
        sort="newest", year_to=2024, email="a@b.c",
    )
    # 自由文本中的逗号/冒号必须清理（filter 语法保留字符）
    assert params["filter"] == (
        "abstract.search:segmentation"
        ",raw_author_name.search:Zhou B."
        ",from_publication_date:2020-01-01"
        ",to_publication_date:2024-12-31"
    )
    assert params["sort"] == "publication_date:desc"
    assert params["mailto"] == "a@b.c"


# ---------------------------------------------------------------------------
# Europe PMC
# ---------------------------------------------------------------------------


def test_europepmc_default_plain_query():
    assert epmc_src.build_query("electron") == "(electron)"


def test_europepmc_title_author_year_oa():
    q = epmc_src.build_query(
        "immune checkpoint", 2022, field="title", author="Smith J",
        year_to=2025, oa_only=True,
    )
    assert q == (
        '(TITLE:"immune checkpoint") AND (AUTH:"Smith J")'
        " AND (PUB_YEAR:[2022 TO 2025]) AND (OPEN_ACCESS:Y)"
    )


def test_europepmc_reserved_chars_sanitized():
    # 引号等保留字符被清理为空格，用户输入无法改变查询语义
    q = epmc_src.build_query('bad"input[field]', field="title")
    assert q == '(TITLE:"bad input field")'


def test_europepmc_sort_params():
    assert "sort" not in epmc_src.build_params("(x)", 10)
    assert epmc_src.build_params("(x)", 10, sort="citations")["sort"] == "CITED desc"
    assert epmc_src.build_params("(x)", 10, sort="newest")["sort"] == "P_PDATE_D desc"


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------


def test_arxiv_default_matches_legacy_behavior():
    assert arxiv_src.build_search_query("electron") == "all:electron"
    assert arxiv_src.build_search_query("graph neural") == 'all:"graph neural"'


def test_arxiv_field_author_year_range():
    q = arxiv_src.build_search_query(
        "diffusion model", 2023, field="title", author='Bad"Name', year_to=2025
    )
    assert 'ti:"diffusion model"' in q
    assert 'au:"Bad Name"' in q
    assert "submittedDate:[202301010000 TO 202512312359]" in q


# ---------------------------------------------------------------------------
# 服务层：参数校验与统一后过滤
# ---------------------------------------------------------------------------


def _paper(sid: str, title: str, *, citations=None, pdf_urls=None):
    from paper_agent.models import Paper

    return Paper(
        source=sid.split(":", 1)[0], source_id=sid, title=title,
        citations=citations, pdf_urls=pdf_urls or [],
    )


def test_search_sources_validates_conditions(tmp_path):
    from paper_agent.library import Library
    from paper_agent.services import search as svc

    lib = Library(tmp_path / "lib.db")
    try:
        for kw in [{"field": "bad"}, {"sort": "bad"}, {"min_citations": -1},
                   {"year_from": 2024, "year_to": 2020}]:
            try:
                svc.search_sources("q", lib=lib, cfg={}, sources="openalex", **kw)
                raise AssertionError(f"{kw} 应当抛 ValueError")
            except ValueError:
                pass
    finally:
        lib.close()


def test_search_sources_post_filter(tmp_path, monkeypatch):
    """min_citations / oa_only 跨源统一后过滤；剔除数量进 notes。"""
    from paper_agent.library import Library
    from paper_agent.services import search as svc

    lib = Library(tmp_path / "lib.db")
    try:
        monkeypatch.setattr(
            svc, "optimize_query",
            lambda q: {"topics": [q], "year_from": None, "max_results": None, "optimized": False},
        )

        def fake_tool(name, **_):
            return ToolResult(tool="search_papers", status="ok", data=[
                _paper("openalex:W1", "Hot Paper", citations=30, pdf_urls=["http://x/1.pdf"]),
                _paper("openalex:W2", "Cold Paper", citations=2, pdf_urls=["http://x/2.pdf"]),
                _paper("openalex:W3", "No Cites", citations=None, pdf_urls=[]),
            ])

        monkeypatch.setattr(svc, "search_source_tool", fake_tool)
        out = svc.search_sources(
            "topic", lib=lib, cfg={"sources": {"openalex": {"enabled": True}}},
            sources="openalex", max_results=10, min_citations=5, oa_only=True,
        )
        assert [c.paper.source_id for c in out.candidates] == ["openalex:W1"]
        assert any("剔除 2" in n for n in out.notes)
        assert any("过滤条件" in n for n in out.notes)
    finally:
        lib.close()