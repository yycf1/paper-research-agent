"""复现产物 API 测试（M24）：工作区文件列表/内容查看 + 白名单 + 运行记录。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_agent import paths
from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.server.app import create_app
from paper_agent.server import routes as rt


class _FakeStore:
    def __init__(self, path):
        pass

    def count(self):
        return 0


def _client(monkeypatch, tmp_path) -> TestClient:
    cfg = {"data_dir": str(tmp_path / "data"), "proxy": "", "sources": {}}
    monkeypatch.setattr(rt, "load_config", lambda: cfg)
    monkeypatch.setattr(rt, "VectorStore", _FakeStore)
    lib = Library(tmp_path / "data" / "library.db")
    lib.upsert_paper(Paper(source="openalex", source_id="openalex:W1", title="Graph Proto"))
    lib.close()
    return TestClient(create_app())


def _seed_repro(tmp_path) -> None:
    base = tmp_path / "data" / "repro" / safe_dirname("openalex:W1")
    ws = base / "workspace"
    ws.mkdir(parents=True)
    (ws / "run_proto.py").write_text("print('proto')\n", encoding="utf-8")
    (ws / "notes.txt").write_text("备注", encoding="utf-8")
    (base / "runs.jsonl").write_text(
        "\n".join([
            '{"ts": 1000, "filename": "v1.py", "exit_code": 1, "timed_out": false, "stdout": "", "stderr": "boom"}',
            '{"ts": 2000, "filename": "v2.py", "exit_code": 0, "timed_out": false, "stdout": "acc=1.0", "stderr": ""}',
        ]) + "\n",
        encoding="utf-8",
    )
    knowledge = paths.knowledge_dir({"data_dir": str(tmp_path / "data")}) / safe_dirname("openalex:W1")
    knowledge.mkdir(parents=True)
    (knowledge / "repro_plan.md").write_text("## 复现目标\n跑通。", encoding="utf-8")


def test_repro_overview(monkeypatch, tmp_path):
    _seed_repro(tmp_path)
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/papers/openalex:W1/repro")
    assert r.status_code == 200
    d = r.json()
    assert "复现目标" in d["plan"]
    assert [f["name"] for f in d["files"]] == ["notes.txt", "run_proto.py"]  # 新→旧
    assert [run["filename"] for run in d["runs"]] == ["v2.py", "v1.py"]  # 新→旧
    assert d["runs"][0]["stdout"] == "acc=1.0"


def test_repro_file_view_and_whitelist(monkeypatch, tmp_path):
    _seed_repro(tmp_path)
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/papers/openalex:W1/repro/file", params={"name": "run_proto.py"})
    assert r.status_code == 200
    assert "proto" in r.json()["content"]

    # 路径穿越 / 非白名单扩展 / 不存在
    assert client.get("/api/papers/openalex:W1/repro/file", params={"name": "../runs.jsonl"}).status_code == 400
    assert client.get("/api/papers/openalex:W1/repro/file", params={"name": "virus.exe"}).status_code == 400
    assert client.get("/api/papers/openalex:W1/repro/file", params={"name": "nope.py"}).status_code == 404


def test_repro_missing_paper_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/papers/openalex:NOPE/repro").status_code == 404


def test_repro_empty_workspace(monkeypatch, tmp_path):
    """没有工作区也不报错：空文件列表 + plan 为 None。"""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/papers/openalex:W1/repro")
    assert r.status_code == 200
    d = r.json()
    assert d["files"] == [] and d["runs"] == [] and d["plan"] is None
