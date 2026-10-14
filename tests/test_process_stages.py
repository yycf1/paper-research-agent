"""process_paper 阶段机测试：断点续跑语义 + experiment.json 产物级复用。

全部离线：重 IO/LLM 依赖（extract_experiment / generate_report / embed /
VectorStore / split_markdown）在 process 模块命名空间内 monkeypatch；
账本用真实 Library 落在 tmp_path。
"""

from __future__ import annotations

from types import SimpleNamespace

from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.services import process as proc


class _FakeStore:
    saved: tuple | None = None

    def __init__(self, path):
        pass

    def paper_modes(self):
        return {}

    def upsert_paper_chunks(self, sid, texts, metas, vectors):
        _FakeStore.saved = (sid, texts, metas)


def _seed_parsed(tmp_path, sid: str = "openalex:W1") -> Library:
    """种子：parsed 状态论文 + 对应 full_text.md（知识提取阶段的输入）。"""
    lib = Library(tmp_path / "data" / "library.db")
    lib.upsert_paper(Paper(source="openalex", source_id=sid, title="Graph Survey",
                           abstract="摘要", pdf_urls=["http://x/a.pdf"]))
    lib.set_status(sid, "parsed")
    md_path = tmp_path / "data" / "parsed" / safe_dirname(sid) / "full_text.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# 全文\n方法与实验描述。", encoding="utf-8")
    return lib


def _patch_llm(monkeypatch, report_fail_first: bool):
    calls = {"report": 0}
    monkeypatch.setattr(proc, "extract_experiment",
                        lambda markdown, chat_fn=None: {"research_goal": "g", "method": "m"})
    monkeypatch.setattr(proc, "split_markdown",
                        lambda md, sid, title: [SimpleNamespace(text="块1", metadata={})])
    monkeypatch.setattr(proc, "embed", lambda texts, **kw: [[0.0, 0.0] for _ in texts])
    monkeypatch.setattr(proc, "VectorStore", _FakeStore)

    def fake_report(markdown, chat_fn=None):
        calls["report"] += 1
        if report_fail_first and calls["report"] == 1:
            raise RuntimeError("LLM 超时")
        return "# 精读报告\n...", {"prompt": 10, "completion": 5}

    monkeypatch.setattr(proc, "generate_report", fake_report)
    return calls


def test_report_failure_keeps_parsed_and_stages(monkeypatch, tmp_path):
    """报告生成失败：状态留在 parsed、report 阶段 failed，前面阶段可见。"""
    _patch_llm(monkeypatch, report_fail_first=True)
    lib = _seed_parsed(tmp_path)
    try:
        out = proc.process_paper("openalex:W1", {"data_dir": str(tmp_path / "data")}, lib)
        assert out["final_status"] == "parsed"
        by_key = {s["stage"]: s for s in out["stages"]}
        assert by_key["download"]["status"] == "skipped"
        assert by_key["parse"]["status"] == "skipped"
        assert by_key["experiment"]["status"] == "ok"
        assert by_key["report"]["status"] == "failed"
        assert "LLM 超时" in by_key["report"]["detail"]
        assert lib.get("openalex:W1").status == "parsed"
        assert lib.get("openalex:W1").error
    finally:
        lib.close()


def test_retry_reuses_experiment_artifact(monkeypatch, tmp_path):
    """重试：experiment.json 未过期则复用（不重复提取），报告补齐后直达 indexed。"""
    _patch_llm(monkeypatch, report_fail_first=True)
    cfg = {"data_dir": str(tmp_path / "data")}
    lib = _seed_parsed(tmp_path)
    try:
        proc.process_paper("openalex:W1", cfg, lib)  # 第一轮：报告失败

        _FakeStore.saved = None
        out = proc.process_paper("openalex:W1", cfg, lib)  # 第二轮：报告成功
        assert out["final_status"] == "indexed"
        by_key = {s["stage"]: s for s in out["stages"]}
        assert by_key["experiment"]["status"] == "skipped"
        assert "复用" in by_key["experiment"]["detail"]
        assert by_key["report"]["status"] == "ok"
        assert by_key["index"]["status"] == "ok" and "1 块" in by_key["index"]["detail"]
        assert _FakeStore.saved is not None and _FakeStore.saved[0] == "openalex:W1"
        assert lib.get("openalex:W1").status == "indexed"
    finally:
        lib.close()


def test_progress_callback_sees_running(monkeypatch, tmp_path):
    """progress 回调在每个阶段状态变化时被调用；每个阶段先 running 再落定终态。"""
    _patch_llm(monkeypatch, report_fail_first=False)
    lib = _seed_parsed(tmp_path)
    try:
        history: dict[str, list[str]] = {}

        def cb(stages):
            for s in stages:
                history.setdefault(s["stage"], []).append(s["status"])

        proc.process_paper("openalex:W1", {"data_dir": str(tmp_path / "data")}, lib, progress=cb)
        assert len(history) == 5  # 5 个阶段全部出现过
        for key in ("download", "parse", "experiment", "report", "index"):
            assert history[key][0] == "running", f"{key} 应先进入 running"
        assert history["download"][-1] == "skipped"
        assert history["experiment"][-1] == "ok"
        assert history["report"][-1] == "ok"
        assert history["index"][-1] == "ok"
    finally:
        lib.close()


def test_no_pdf_fails_download(monkeypatch, tmp_path):
    """无 OA 链接：download 阶段 failed，后续阶段不执行。"""
    lib = Library(tmp_path / "data" / "library.db")
    lib.upsert_paper(Paper(source="openalex", source_id="openalex:W9", title="No PDF"))
    try:
        out = proc.process_paper("openalex:W9", {"data_dir": str(tmp_path / "data")}, lib)
        assert out["final_status"] == "discovered"
        by_key = {s["stage"]: s for s in out["stages"]}
        assert by_key["download"]["status"] == "failed"
        assert "仅摘要" in by_key["download"]["detail"]
        assert "report" not in by_key  # 后续阶段未启动
    finally:
        lib.close()


def test_download_failed_status_retries(monkeypatch, tmp_path):
    """download_failed 的论文重试时应重走下载（旧版全部跳过、永远无法重试）。"""

    def fake_download(paper, papers_dir, proxy=""):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(proc, "download_pdf", fake_download)
    lib = Library(tmp_path / "data" / "library.db")
    lib.upsert_paper(Paper(source="openalex", source_id="openalex:W8", title="Retry Me",
                           pdf_urls=["http://x/a.pdf"]))
    lib.set_status("openalex:W8", "download_failed", error="旧错误")
    try:
        out = proc.process_paper("openalex:W8", {"data_dir": str(tmp_path / "data"), "proxy": ""}, lib)
        by_key = {s["stage"]: s for s in out["stages"]}
        assert by_key["download"]["status"] == "failed"  # 真的重试了
        assert "403" in by_key["download"]["detail"]
        assert out["final_status"] == "download_failed"
    finally:
        lib.close()
