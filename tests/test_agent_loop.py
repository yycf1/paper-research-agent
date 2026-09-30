"""Agent loop 离线测试：脚本化假模型驱动循环，验证协议回填、错误纠正、熔断。

不联网、不调真实 LLM：chat_fn 返回预编排的 ChatResponse 序列，
循环协议（assistant tool_calls 回填 / role=tool 结果配对）用捕获的
messages 断言——这正是 function-calling 循环最需要回归的部分。
"""

from __future__ import annotations

import json

import pytest

from paper_agent.agent import executor as executor_mod
from paper_agent.agent.executor import ToolExecutor, parse_pick_spec
from paper_agent.agent.loop import run_agent_task
from paper_agent.library import Library
from paper_agent.llm import ChatResponse, ToolCall
from paper_agent.models import Paper
from paper_agent.services.search import Candidate, PreviewResult
from paper_agent.sources import SourceUnavailable
from paper_agent.tools import TOOL_REGISTRY

TASK = "找 2 篇 GNN 综述入库"


def _paper(sid: str, title: str, doi: str | None = None) -> Paper:
    return Paper(source=sid.split(":", 1)[0], source_id=sid, title=title, doi=doi,
                 abstract="abstract text", year=2024, citations=5, pdf_urls=["http://x/y.pdf"])


def _preview() -> PreviewResult:
    return PreviewResult(
        candidates=[
            Candidate(paper=_paper("openalex:W1", "Paper One", doi="10.1/1")),
            Candidate(paper=_paper("europepmc:PMC2", "Paper Two"), duplicate_of="openalex:W0"),
        ],
        total_hits=12,
        notes=["europepmc 不可用，已跳过"],
    )


def _tool_resp(name: str, args_json: str, call_id: str = "c1") -> ChatResponse:
    return ChatResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args_json)],
        usage={"prompt": 100, "completion": 20, "model": "fake"},
    )


def _final(text: str) -> ChatResponse:
    return ChatResponse(content=text, tool_calls=[], usage={"prompt": 100, "completion": 20, "model": "fake"})


def _scripted_chat(script: list[ChatResponse], captured: list | None = None):
    """按脚本依次返回；脚本耗尽或 tools 为空（熔断收尾）时返回兜底最终回答。"""

    def _chat(messages, *, tools):
        if captured is not None:
            captured.append((list(messages), tools))
        if not tools:
            return _final("（收尾总结）")
        return script.pop(0) if script else _final("（脚本兜底）")

    return _chat


@pytest.fixture()
def lib(tmp_path):
    library = Library(tmp_path / "lib.db")
    yield library
    library.close()


def _executor(lib, monkeypatch, preview=None):
    if preview is not None:
        monkeypatch.setattr(executor_mod, "search_sources", lambda *a, **kw: preview)
    return ToolExecutor(lib=lib, cfg={"proxy": "", "sources": {}})


def test_full_loop_search_then_add(lib, monkeypatch):
    """检索 → 挑选入库 → 最终回答：轨迹、入库结果与消息协议全部正确。"""
    ex = _executor(lib, monkeypatch, preview=_preview())
    captured: list = []
    chat_fn = _scripted_chat([
        _tool_resp("search_papers", '{"query": "graph neural network survey", "year_from": 2024}', "c1"),
        _tool_resp("add_papers", '{"pick": "1"}', "c2"),
    ], captured)

    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)

    assert result.stopped_reason == "final"
    assert result.answer == "（脚本兜底）"
    assert [s.tool for s in result.steps] == ["search_papers", "add_papers"]
    assert all(s.status == "ok" for s in result.steps)
    # add_papers 只收新候选（pick=1 是新论文，2 是库内已有）
    assert lib.get("openalex:W1") is not None
    assert lib.get("europepmc:PMC2") is None
    # 协议：第二次调用前，上下文里有 assistant tool_calls 声明 + 配对的 tool 结果
    msgs2, _ = captured[1]
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in msgs2)
    tool_msgs = [m for m in msgs2 if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1"]
    assert '"status": "ok"' in tool_msgs[0]["content"]
    # 工具注册表以 OpenAI specs 形式传入
    _, tools1 = captured[0]
    assert {t["function"]["name"] for t in tools1} == set(TOOL_REGISTRY)
    assert all(t["type"] == "function" for t in tools1)
    # usage 跨步累计
    assert result.usage["prompt"] == 300 and result.usage["completion"] == 60


def test_unknown_tool_fed_back_and_recovered(lib, monkeypatch):
    """模型调了不存在的工具：fatal 回填错误与可用清单，循环继续到最终回答。"""
    ex = _executor(lib, monkeypatch)
    captured: list = []
    chat_fn = _scripted_chat([
        _tool_resp("search_papers_all", "{}", "c1"),
    ], captured)

    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)

    assert result.stopped_reason == "final"
    assert result.steps[0].status == "fatal"
    assert "未知工具" in result.steps[0].detail
    assert "search_papers" in result.steps[0].detail  # 错误里带可用工具清单
    msgs2, _ = captured[1]
    tool_msgs = [m for m in msgs2 if m["role"] == "tool"]
    assert '"status": "fatal"' in tool_msgs[0]["content"]


def test_invalid_json_args_fatal(lib, monkeypatch):
    """参数不是合法 JSON：fatal 回填，模型可纠正。"""
    ex = _executor(lib, monkeypatch)
    chat_fn = _scripted_chat([_tool_resp("search_papers", "not-json{", "c1")])
    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)
    assert result.steps[0].status == "fatal"
    assert "JSON" in result.steps[0].detail


def test_degraded_source_error_fed_back(lib, monkeypatch):
    """数据源故障：run_tool 映射 degraded，错误回填后模型继续收尾。"""
    def _boom(*a, **kw):
        raise SourceUnavailable("openalex 被限流")
    monkeypatch.setattr(executor_mod, "search_sources", _boom)
    ex = ToolExecutor(lib=lib, cfg={"proxy": "", "sources": {}})
    captured: list = []
    chat_fn = _scripted_chat([_tool_resp("search_papers", '{"query": "gnn"}', "c1")], captured)

    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)

    assert result.steps[0].status == "degraded"
    assert "限流" in result.steps[0].detail
    msgs2, _ = captured[1]
    tool_msgs = [m for m in msgs2 if m["role"] == "tool"]
    assert '"status": "degraded"' in tool_msgs[0]["content"]


def test_max_steps_soft_landing(lib, monkeypatch):
    """步数熔断：每步都调工具，触顶后强制无工具总结（tools=[] 的收尾调用）。"""
    ex = _executor(lib, monkeypatch)
    script = [_tool_resp("library_status", "{}", f"c{i}") for i in range(1, 4)]
    captured: list = []
    chat_fn = _scripted_chat(script, captured)

    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=3)

    assert result.stopped_reason == "max_steps"
    assert len(result.steps) == 3
    assert result.answer == "（收尾总结）"
    # 收尾调用的 tools 是空列表（不允许再调工具），且追加了熔断提示
    last_messages, last_tools = captured[-1]
    assert last_tools == []
    assert any("工具调用限制" in m.get("content", "") for m in last_messages if m["role"] == "user")


def test_llm_failure_converted_to_error_result(lib, monkeypatch):
    """LLM 层故障（如连续限流）不向上抛：转成 stopped_reason=error 的结果。"""

    def _boom(messages, *, tools):
        raise RuntimeError("LLM 连续 4 次被限流")

    result = run_agent_task(TASK, executor=_executor(lib, monkeypatch), chat_fn=_boom, max_steps=5)
    assert result.stopped_reason == "error"
    assert "限流" in result.answer


def test_executor_covers_registry(lib):
    """执行器实现与注册表一一对应（新增工具漏实现会在测试里暴露）。"""
    ex = ToolExecutor(lib=lib, cfg={"proxy": "", "sources": {}})
    assert set(ex.supported_tools) == set(TOOL_REGISTRY)
    for name in TOOL_REGISTRY:
        result = ex.execute(name, "{}")
        assert result.status in {"ok", "degraded", "fatal"}, f"{name} 应可执行而非崩溃"


def test_parse_pick_spec():
    assert parse_pick_spec("1,3-5", 10) == [1, 3, 4, 5]
    assert parse_pick_spec("all", 3) == []  # 非序号表达式解析为空（all 由调用方特判）
    assert parse_pick_spec("9,99", 3) == []  # 越界序号全部跳过
    assert parse_pick_spec("2-1", 3) == [1, 2]  # 倒序区间自动纠正


# ---------------------------------------------------------------------------
# M15 护栏：循环检测 / token 预算 / 入库上限与确认钩子 / 限流自动重试 / 轨迹
# ---------------------------------------------------------------------------


def test_repeat_call_warned_then_blocked(lib, monkeypatch):
    """同签名第 2 次执行但带警告，第 3 次拒绝执行（不再触碰真实工具）。"""
    ex = _executor(lib, monkeypatch, preview=_preview())
    calls = {"n": 0}

    def fake_search(*a, **kw):
        calls["n"] += 1
        return _preview()

    monkeypatch.setattr(executor_mod, "search_sources", fake_search)
    captured: list = []
    chat_fn = _scripted_chat([
        _tool_resp("search_papers", '{"query": "gnn"}', "c1"),
        _tool_resp("search_papers", '{"query": "gnn"}', "c2"),
        _tool_resp("search_papers", '{"query": "gnn"}', "c3"),
    ], captured)

    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)

    assert calls["n"] == 2  # 第 3 次未触达真实工具
    assert [s.status for s in result.steps] == ["ok", "ok", "fatal"]
    assert "第 2 次完全相同" in result.steps[1].detail
    assert "已拒绝执行" in result.steps[2].detail
    # 警告随 tool 消息回填给模型
    msgs3, _ = captured[2]
    tool_msgs = [m for m in msgs3 if m["role"] == "tool"]
    assert any("warning" in m["content"] for m in tool_msgs)


def test_token_budget_soft_landing(lib, monkeypatch):
    """token 超预算：下一轮调用前拦截，强制无工具总结，reason=token_budget。"""
    ex = _executor(lib, monkeypatch)

    def _expensive(messages, *, tools):
        captured.append((list(messages), tools))
        if not tools:
            return _final("（预算收尾总结）")
        return _tool_resp("library_status", "{}", f"c{len(captured)}")

    captured: list = []
    result = run_agent_task(TASK, executor=ex, chat_fn=_expensive, max_steps=5, token_budget=50)

    assert result.stopped_reason == "token_budget"
    assert result.answer == "（预算收尾总结）"
    assert len(result.steps) == 1  # 第一步花了 120 tokens，第二步前被预算拦截
    last_messages, last_tools = captured[-1]
    assert last_tools == []
    assert any("token 预算" in m.get("content", "") for m in last_messages if m["role"] == "user")


def test_add_cap_rejects_oversized_batch(lib, monkeypatch):
    """单次入库超过 add_cap：fatal 拒绝并提示分批，不静默截断。"""
    many = PreviewResult(
        candidates=[Candidate(paper=_paper(f"openalex:W{i}", f"Paper {i}")) for i in range(1, 26)],
        total_hits=25,
    )
    ex = _executor(lib, monkeypatch, preview=many)
    ex._add_cap = 20  # 显式设小，模拟 config
    ex._preview = many.candidates  # 直接注入预览状态（跳过 search 步骤，聚焦上限逻辑）
    chat_fn = _scripted_chat([
        _tool_resp("add_papers", '{"pick": "all"}', "c1"),
    ])
    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)
    assert result.steps[0].status == "fatal"
    assert "超过上限" in result.steps[0].detail
    assert len(lib.list()) == 0  # 一篇都没入库


def test_confirm_hook_gates_bulk_ingest(lib, monkeypatch):
    """确认钩子：拒绝则不入库且回填 declined；同意则正常入库。"""
    five = PreviewResult(
        candidates=[Candidate(paper=_paper(f"openalex:W{i}", f"Paper {i}")) for i in range(1, 6)],
        total_hits=5,
    )
    asked: list[int] = []

    def deny(count, titles):
        asked.append(count)
        return False

    ex = _executor(lib, monkeypatch, preview=five)
    ex._confirm_threshold = 1
    ex._confirm = deny
    ex._preview = five.candidates
    chat_fn = _scripted_chat([_tool_resp("add_papers", '{"pick": "1-3"}', "c1")])
    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)
    assert asked == [3]
    assert result.steps[0].status == "ok"
    assert len(lib.list()) == 0  # 拒绝后未入库

    def approve(count, titles):
        return True

    ex2 = _executor(lib, monkeypatch, preview=five)
    ex2._confirm_threshold = 1
    ex2._confirm = approve
    ex2._preview = five.candidates
    chat_fn = _scripted_chat([_tool_resp("add_papers", '{"pick": "1-3"}', "c2")])
    run_agent_task(TASK, executor=ex2, chat_fn=chat_fn, max_steps=5)
    assert len(lib.list()) == 3


def test_retryable_rate_limit_auto_retried(lib, monkeypatch):
    """限流（retryable_error）由循环层自动重试一次；非限流 degraded 不重试。"""
    monkeypatch.setattr("paper_agent.agent.loop.time.sleep", lambda s: None)
    attempts = {"n": 0}

    def flaky_search(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise SourceUnavailable("OpenAlex HTTP 429: rate limited")
        return _preview()

    monkeypatch.setattr(executor_mod, "search_sources", flaky_search)
    ex = ToolExecutor(lib=lib, cfg={"proxy": "", "sources": {}})
    chat_fn = _scripted_chat([_tool_resp("search_papers", '{"query": "gnn"}', "c1")])
    result = run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5)
    assert attempts["n"] == 2  # 自动重试了一次
    assert result.steps[0].status == "ok"
    assert result.steps[0].detail.startswith("（自动重试后）")


def test_run_tool_classifies_rate_limit():
    """run_tool 分类器：429/限流 → retryable_error 带 retry_after；其余 → degraded。"""
    from paper_agent.tools import run_tool

    def _boom_429():
        raise SourceUnavailable("OpenAlex HTTP 429: too many requests")

    def _boom_down():
        raise SourceUnavailable("请求失败（含 1 次重试）：ConnectionError")

    r1 = run_tool("search_papers", _boom_429)
    assert r1.status == "retryable_error"
    assert r1.retry_after == 15
    r2 = run_tool("search_papers", _boom_down)
    assert r2.status == "degraded"


def test_tool_level_variation_loop_warned(lib, monkeypatch):
    """工具级计数：换参数的变体循环（签名各不相同）第 4 次起注入推进提醒。"""
    ex = _executor(lib, monkeypatch, preview=_preview())
    captured: list = []
    chat_fn = _scripted_chat([
        _tool_resp("search_papers", '{"query": "a"}', "c1"),
        _tool_resp("search_papers", '{"query": "b"}', "c2"),
        _tool_resp("search_papers", '{"query": "c"}', "c3"),
        _tool_resp("search_papers", '{"query": "d"}', "c4"),
    ], captured)

    run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=6)

    # 前 3 次调用的结果无 warning；第 4 次的结果（出现在其后的下一轮上下文中）带推进提醒
    assert not any(
        "第 4 次调用" in x["content"]
        for msgs, _ in captured[:4] for x in msgs if x["role"] == "tool"
    )
    assert any(
        "第 4 次调用 search_papers" in x["content"]
        for msgs, _ in captured[4:] for x in msgs if x["role"] == "tool"
    )


def test_search_all_sources_degraded_warns(lib, monkeypatch):
    """全源故障 0 结果：工具结果带显式 warning，不再伪装成正常空结果。"""
    degraded = PreviewResult(
        candidates=[],
        total_hits=0,
        notes=["openalex 不可用（429）"],
        degraded_sources={"openalex": "HTTP 429", "europepmc": "HTTP 503"},
    )
    ex = _executor(lib, monkeypatch, preview=degraded)
    out = ex.execute("search_papers", '{"query": "gnn"}')
    assert out.status == "ok"
    assert "warning" in out.data
    assert "数据源故障" in out.data["warning"]
    assert sorted(out.data["degraded_sources"]) == ["europepmc", "openalex"]


def test_trace_persisted_jsonl(lib, monkeypatch, tmp_path):
    """trace 逐步落盘：task / step / final 三类事件齐全，JSON 可解析。"""
    ex = _executor(lib, monkeypatch, preview=_preview())
    trace_path = tmp_path / "traces" / "run.jsonl"
    chat_fn = _scripted_chat([
        _tool_resp("search_papers", '{"query": "gnn"}', "c1"),
    ])
    run_agent_task(TASK, executor=ex, chat_fn=chat_fn, max_steps=5, trace_path=trace_path)

    lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    types = [line["type"] for line in lines]
    assert types[0] == "task" and lines[0]["task"] == TASK
    assert types.count("step") == 1
    assert types[-1] == "final"
    assert lines[-1]["stopped_reason"] == "final"
