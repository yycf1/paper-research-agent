"""复现规划器测试：离线注入假 LLM，验证输入契约与产物落盘。"""

from __future__ import annotations

import json

import pytest

from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.services.repro import generate_repro_plan, repro_plan_path


def _paper(sid: str, title: str) -> Paper:
    return Paper(source=sid.split(":", 1)[0], source_id=sid, title=title, abstract="摘要")


def _seed_analyzed(tmp_path, sid="openalex:W1", with_text=True):
    lib = Library(tmp_path / "data" / "library.db")
    lib.upsert_paper(_paper(sid, "Repro Paper"))
    lib.set_status(sid, "analyzed")
    kd = tmp_path / "data" / "knowledge" / safe_dirname(sid)
    kd.mkdir(parents=True, exist_ok=True)
    (kd / "experiment.json").write_text(
        json.dumps({
            "research_goal": "验证 GNN 推荐效果",
            "repro_assets": {"code_url": ["https://github.com/a/b"], "dataset_urls": [], "project_page": []},
            "token_usage": {"prompt": 1, "completion": 1},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    if with_text:
        pd = tmp_path / "data" / "parsed" / safe_dirname(sid)
        pd.mkdir(parents=True, exist_ok=True)
        (pd / "full_text.md").write_text("论文正文。" * 50, encoding="utf-8")
    return lib


def _cfg(tmp_path):
    return {"data_dir": str(tmp_path / "data"), "proxy": "", "sources": {}}


def test_generate_repro_plan_writes_file(tmp_path):
    lib = _seed_analyzed(tmp_path)
    try:
        calls = []

        def fake_chat(messages, **kw):
            calls.append(messages)
            return (
                "## 复现目标\n复现 GNN 推荐实验，对齐 AUC 0.95。\n\n## 风险与缺口\n文中未说明。",
                {"prompt": 100, "completion": 50, "model": "fake"},
            )

        cfg = _cfg(tmp_path)
        plan, usage = generate_repro_plan("openalex:W1", cfg=cfg, lib=lib, chat_fn=fake_chat)
        assert plan.startswith("## 复现目标")
        assert usage["completion"] == 50
        # 输入契约：experiment.json 全文进 prompt，论文节选也带上
        assert "验证 GNN 推荐效果" in calls[0][0]["content"]
        assert "github.com/a/b" in calls[0][0]["content"]
        assert "论文正文。" in calls[0][0]["content"]
        out = repro_plan_path(cfg, "openalex:W1")
        assert out.exists() and out.read_text(encoding="utf-8").startswith("## 复现目标")
    finally:
        lib.close()


def test_generate_repro_plan_requires_experiment(tmp_path):
    lib = Library(tmp_path / "data" / "library.db")
    try:
        lib.upsert_paper(_paper("openalex:W2", "No Experiment"))
        with pytest.raises(ValueError, match="experiment.json"):
            generate_repro_plan("openalex:W2", cfg=_cfg(tmp_path), lib=lib, chat_fn=lambda m, **k: ("x", {}))
    finally:
        lib.close()


def test_generate_repro_plan_unknown_paper(tmp_path):
    lib = Library(tmp_path / "data" / "library.db")
    try:
        with pytest.raises(ValueError, match="未找到论文"):
            generate_repro_plan("openalex:NOPE", cfg=_cfg(tmp_path), lib=lib, chat_fn=lambda m, **k: ("x", {}))
    finally:
        lib.close()
