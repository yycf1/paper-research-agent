"""任务锚定与工具作用域测试（M26）：M25 漂移事故的系统性修复。

覆盖：锚定执行器的工具面裁剪与执行拦截、循环层锚定约束注入、
run_code -u 无缓冲（超时也能拿到部分输出）。
"""

from __future__ import annotations

import json
from pathlib import Path

from paper_agent import paths
from paper_agent.agent.executor import ANCHOR_DENIED_TOOLS, ToolExecutor
from paper_agent.agent.loop import run_agent_task
from paper_agent.download import safe_dirname
from paper_agent.library import Library
from paper_agent.llm import ChatResponse, ToolCall
from paper_agent.models import Paper


def _seed_lib(tmp_path) -> Library:
    lib = Library(tmp_path / "library.db")
    lib.upsert_paper(Paper(source="openalex", source_id="openalex:W1", title="T-W1"))
    lib.set_status("openalex:W1", "indexed")
    return lib


def _executor(tmp_path, *, lib=None, anchor=None) -> ToolExecutor:
    return ToolExecutor(
        lib=lib or _seed_lib(tmp_path),
        cfg={"data_dir": str(tmp_path / "data"), "agent": {}},
        anchor=anchor,
    )


def test_anchor_scopes_tool_surface(tmp_path):
    """锚定任务：检索/入库/方向推荐从工具面移除，调用被拦截为 fatal。"""
    ex = _executor(tmp_path, anchor="openalex:W1")
    names = {s["function"]["name"] for s in ex.specs_for()}
    assert ANCHOR_DENIED_TOOLS.isdisjoint(names)
    assert "read_repro_plan" in names and "write_code" in names and "run_code" in names

    r = ex.execute("search_papers", json.dumps({"query": "x"}))
    assert r.status == "fatal"
    assert "锚定" in r.error and "openalex:W1" in r.error
    # 被拒绝的调用不产生副作用语义（fatal 不重试）
    assert r.data is None


def test_no_anchor_keeps_full_tool_surface(tmp_path):
    ex = _executor(tmp_path)
    names = {s["function"]["name"] for s in ex.specs_for()}
    assert names == set(ex.supported_tools)


def test_loop_injects_anchor_constraint(tmp_path):
    """锚定约束注入系统提示，且模型看到的工具面不含作用域外工具。"""
    captured: dict = {}

    def fake_chat(messages, *, tools, **_):
        captured["system"] = messages[0]["content"]
        captured["tools"] = [t["function"]["name"] for t in tools]
        return ChatResponse(content="done", tool_calls=[], usage={"prompt": 1, "completion": 1, "model": "f"})

    ex = _executor(tmp_path, anchor="openalex:W1")
    result = run_agent_task(
        "继续完善复现", executor=ex, cfg={"data_dir": str(tmp_path / "data"), "agent": {}},
        chat_fn=fake_chat, anchor="openalex:W1",
    )
    assert result.stopped_reason == "final"
    assert "任务锚定" in captured["system"] and "openalex:W1" in captured["system"]
    assert ANCHOR_DENIED_TOOLS.isdisjoint(captured["tools"])


def test_run_code_unbuffered_timeout_captures_output(tmp_path):
    """run_code 以 -u 运行：超时杀进程前已打印的内容可读（M25 缓冲假象的修复）。"""
    ex = _executor(tmp_path)
    ex.execute("write_code", json.dumps({
        "source_id": "openalex:W1", "filename": "noisy.py",
        "content": "print('phase-1 done')\nimport time\ntime.sleep(30)\n",
    }))
    r = ex.execute("run_code", json.dumps({
        "source_id": "openalex:W1", "filename": "noisy.py", "timeout_sec": 5,
    }))
    assert r.ok and r.data["timed_out"] is True
    assert "phase-1 done" in r.data["stdout"]  # 修复前这里是空的
    # 超时执行同样入运行档案
    runs_path = (
        paths.data_dir({"data_dir": str(tmp_path / "data")})
        / "repro" / safe_dirname("openalex:W1") / "runs.jsonl"
    )
    records = [json.loads(x) for x in runs_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["timed_out"] is True
