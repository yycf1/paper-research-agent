"""复现执行器与跨论文对比工具测试（M19）：全离线（子进程跑真实 python）。

覆盖：compare_papers 的缺省选文/证据分组/缺失 id、read_repro_plan 的
缺失引导、write_code/run_code 的 happy path、HITL 拒绝、超时反馈、
文件名白名单（路径穿越/非 .py 一律 fatal）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from paper_agent import paths
from paper_agent.agent.executor import ToolExecutor
from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.models import Paper


def _seed_lib(tmp_path, sids: list[str]) -> Library:
    lib = Library(tmp_path / "library.db")
    for sid in sids:
        lib.upsert_paper(Paper(source=sid.split(":", 1)[0], source_id=sid, title=f"T-{sid}"))
        lib.set_status(sid, "indexed")
    return lib


def _executor(tmp_path, *, lib=None, retriever=None, confirm=None) -> ToolExecutor:
    return ToolExecutor(
        lib=lib or _seed_lib(tmp_path, ["openalex:W1"]),
        retriever=retriever,
        cfg={"data_dir": str(tmp_path / "data"), "agent": {}},
        confirm=confirm,
    )


def _hit(text: str, section: str = "3 Method", page: int = 5) -> SimpleNamespace:
    return SimpleNamespace(text=text, section=section, page=page, title="t", paper_id="p")


# ---------------------------------------------------------------------------
# compare_papers
# ---------------------------------------------------------------------------


def test_compare_accepts_unique_id_without_source_prefix(tmp_path):
    lib = _seed_lib(tmp_path, ["openalex:W1", "openalex:W2"])
    ex = _executor(tmp_path, lib=lib, retriever=_fake_retriever({"openalex:W1": [_hit("方法A")]}))
    r = ex.execute("compare_papers", json.dumps({"topic": "方法", "paper_ids": ["W1"]}))
    assert r.ok and r.data["missing"] == []
    assert r.data["papers"][0]["paper_id"] == "openalex:W1"
    assert r.data["papers"][0]["evidence"]


def test_compare_rejects_ambiguous_source_suffix(tmp_path):
    lib = _seed_lib(tmp_path, ["openalex:W1", "local:W1"])
    ex = _executor(tmp_path, lib=lib, retriever=_fake_retriever({}))
    r = ex.execute("compare_papers", json.dumps({"topic": "方法", "paper_ids": ["W1"]}))
    assert r.status == "fatal" and "匹配多篇" in r.error


def test_compare_does_not_truncate_dataset_evidence_at_280_chars(tmp_path):
    lib = _seed_lib(tmp_path, ["openalex:W1"])
    evidence = "Context. " * 50 + "Datasets: Beibei, Taobao, Yelp."
    ex = _executor(tmp_path, lib=lib, retriever=_fake_retriever({"openalex:W1": [_hit(evidence)]}))
    r = ex.execute("compare_papers", json.dumps({"topic": "datasets", "paper_ids": ["W1"]}))
    assert "Beibei, Taobao, Yelp" in r.data["papers"][0]["evidence"][0]["text"]


def _fake_retriever(hits_by_paper: dict[str, list]) -> SimpleNamespace:
    return SimpleNamespace(
        store=SimpleNamespace(count=lambda: 42),
        retrieve=lambda query, k=5, paper_id=None: hits_by_paper.get(paper_id, [])[:k],
    )


def test_compare_explicit_ids_groups_evidence(tmp_path):
    lib = _seed_lib(tmp_path, ["openalex:W1", "openalex:W2"])
    retriever = _fake_retriever({
        "openalex:W1": [_hit("方法A 用 GNN", "3 Method", 4), _hit("指标 89.2", "5 Results", 8)],
        "openalex:W2": [_hit("方法B 用 Transformer", "3.1", 5)],
    })
    ex = _executor(tmp_path, lib=lib, retriever=retriever)
    r = ex.execute("compare_papers", json.dumps({
        "topic": "方法对比", "paper_ids": ["openalex:W1", "openalex:W2"], "k_per_paper": 2,
    }))
    assert r.ok
    assert [p["paper_id"] for p in r.data["papers"]] == ["openalex:W1", "openalex:W2"]
    assert len(r.data["papers"][0]["evidence"]) == 2
    assert r.data["papers"][0]["evidence"][0]["page"] == 4


def test_compare_default_picks_recent_processed(tmp_path):
    lib = _seed_lib(tmp_path, ["openalex:W1", "openalex:W2"])
    lib.upsert_paper(Paper(source="openalex", source_id="openalex:W3", title="T3"))  # discovered，不该入选
    retriever = _fake_retriever({"openalex:W1": [_hit("x")], "openalex:W2": [_hit("y")]})
    ex = _executor(tmp_path, lib=lib, retriever=retriever)
    r = ex.execute("compare_papers", json.dumps({"topic": "主题"}))
    assert r.ok
    assert sorted(p["paper_id"] for p in r.data["papers"]) == ["openalex:W1", "openalex:W2"]
    assert r.data["papers"][0]["evidence"]  # 每篇都有证据块


def test_compare_reports_missing_ids(tmp_path):
    lib = _seed_lib(tmp_path, ["openalex:W1"])
    retriever = _fake_retriever({"openalex:W1": [_hit("x")]})
    ex = _executor(tmp_path, lib=lib, retriever=retriever)
    r = ex.execute("compare_papers", json.dumps({
        "topic": "t", "paper_ids": ["openalex:W1", "openalex:NOPE"],
    }))
    assert r.ok and r.data["missing"] == ["openalex:NOPE"]
    assert len(r.data["papers"]) == 1


# ---------------------------------------------------------------------------
# read_repro_plan / write_code / run_code
# ---------------------------------------------------------------------------


def test_read_plan_requires_existing_plan(tmp_path):
    ex = _executor(tmp_path)
    r = ex.execute("read_repro_plan", json.dumps({"source_id": "openalex:W1"}))
    assert r.status == "fatal" and "生成复现方案" in r.error

    knowledge = paths.knowledge_dir({"data_dir": str(tmp_path / "data")}) / safe_dirname("openalex:W1")
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "repro_plan.md").write_text("## 复现目标\n跑通基线。", encoding="utf-8")
    r = ex.execute("read_repro_plan", json.dumps({"source_id": "openalex:W1"}))
    assert r.ok and "复现目标" in r.data["repro_plan"]


def test_write_and_run_code_happy_path(tmp_path):
    ex = _executor(tmp_path)
    r = ex.execute("write_code", json.dumps({
        "source_id": "openalex:W1", "filename": "hello.py",
        "content": "print('hi', 40 + 2)\n",
    }))
    assert r.ok and r.data["bytes"] > 0

    r = ex.execute("run_code", json.dumps({"source_id": "openalex:W1", "filename": "hello.py"}))
    assert r.ok
    assert r.data["ran"] is True and r.data["exit_code"] == 0
    assert "hi 42" in r.data["stdout"]

    # M24：真实执行落运行档案（工作区旁 runs.jsonl），UI 从这里读
    runs_path = paths.data_dir({"data_dir": str(tmp_path / "data")}) / "repro" / safe_dirname("openalex:W1") / "runs.jsonl"
    records = [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["filename"] == "hello.py" and records[0]["exit_code"] == 0
    assert "hi 42" in records[0]["stdout"]


def test_run_code_reports_runtime_error_as_ok_payload(tmp_path):
    """代码崩溃 ≠ 工具失败：ok 结果带非零 exit_code 与 stderr，供模型自纠错。"""
    ex = _executor(tmp_path)
    ex.execute("write_code", json.dumps({
        "source_id": "openalex:W1", "filename": "boom.py",
        "content": "raise ValueError('bad input')\n",
    }))
    r = ex.execute("run_code", json.dumps({"source_id": "openalex:W1", "filename": "boom.py"}))
    assert r.ok  # 工具本身执行成功
    assert r.data["exit_code"] != 0
    assert "ValueError" in r.data["stderr"] and "bad input" in r.data["stderr"]


def test_run_code_timeout_feedback(tmp_path):
    ex = _executor(tmp_path)
    ex.execute("write_code", json.dumps({
        "source_id": "openalex:W1", "filename": "slow.py",
        "content": "import time\ntime.sleep(30)\n",
    }))
    r = ex.execute("run_code", json.dumps({
        "source_id": "openalex:W1", "filename": "slow.py", "timeout_sec": 5,
    }))
    assert r.ok and r.data["timed_out"] is True and r.data["exit_code"] is None
    # 超时也发生了真实执行：同样入运行档案
    runs_path = paths.data_dir({"data_dir": str(tmp_path / "data")}) / "repro" / safe_dirname("openalex:W1") / "runs.jsonl"
    records = [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["timed_out"] is True


def test_run_code_declined_by_confirm(tmp_path):
    ex = _executor(tmp_path, confirm=lambda count, titles: False)
    ex.execute("write_code", json.dumps({
        "source_id": "openalex:W1", "filename": "x.py", "content": "print(1)\n",
    }))
    r = ex.execute("run_code", json.dumps({"source_id": "openalex:W1", "filename": "x.py"}))
    assert r.ok and r.data["declined"] is True and r.data["ran"] is False
    # 被拒绝的执行没有发生：不留运行档案
    runs_path = paths.data_dir({"data_dir": str(tmp_path / "data")}) / "repro" / safe_dirname("openalex:W1") / "runs.jsonl"
    assert not runs_path.exists()


def test_code_filename_whitelist(tmp_path):
    """路径穿越与非 .py 文件一律 fatal（写与执行两侧都拦）。"""
    ex = _executor(tmp_path)
    for bad in ("../escape.py", "sub/dir/x.py", "payload.txt"):
        r = ex.execute("write_code", json.dumps({
            "source_id": "openalex:W1", "filename": bad, "content": "print(1)",
        }))
        assert r.status == "fatal", bad
    r = ex.execute("run_code", json.dumps({"source_id": "openalex:W1", "filename": "x.sh"}))
    assert r.status == "fatal"
    # 恶意文件名不应在工作区留下任何东西
    ws = paths.data_dir({"data_dir": str(tmp_path / "data")}) / "repro" / safe_dirname("openalex:W1") / "workspace"
    assert not ws.exists() or list(ws.rglob("*")) == []
