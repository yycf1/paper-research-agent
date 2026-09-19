"""提取模块离线测试：注入假 LLM，不联网、不花钱。"""

import pytest

from paper_agent.extraction.experiment_flow import (
    extract_experiment,
    extract_repro_urls,
    merge_repro_assets,
    normalize_experiment,
)
from paper_agent.extraction.report import generate_report
from paper_agent.llm import parse_json_reply


def test_extract_repro_urls():
    text = (
        "Code at https://github.com/foo/bar and https://www.github.com/foo/bar. "
        "Data: https://zenodo.org/record/123 and https://huggingface.co/datasets/x/y. "
        "Home: https://example-page.org/project"
    )
    urls = extract_repro_urls(text)
    assert urls["code_url"] == ["https://github.com/foo/bar", "https://www.github.com/foo/bar"]
    assert urls["dataset_urls"] == ["https://zenodo.org/record/123", "https://huggingface.co/datasets/x/y"]


def test_merge_repro_assets_dedup():
    merged = merge_repro_assets(
        {"code_url": ["https://github.com/a/b"], "dataset_urls": [], "project_page": []},
        {"code_url": ["https://github.com/a/b", "https://gitee.com/x/y"], "dataset_urls": ["https://zenodo.org/1"]},
    )
    assert merged["code_url"] == ["https://github.com/a/b", "https://gitee.com/x/y"]
    assert merged["dataset_urls"] == ["https://zenodo.org/1"]
    assert merged["project_page"] == []


def fake_chat_ok(messages, **kw):
    reply = """```json
    {
      "research_goal": "预测药物相互作用",
      "method": {"name": "GNN-DDI", "architecture": "消息传递图网络", "key_components": ["对比学习"]},
      "datasets": [{"name": "DrugBank", "source": "公开", "split": "8:1:1"}],
      "hyperparameters": {"lr": 0.001},
      "experiment_steps": [{"step": 1, "description": "预处理", "details": ""}],
      "metrics": [{"name": "AUC", "value": "0.95", "baseline": "DeepDDI 0.93"}],
      "main_results": "AUC 0.95",
      "conclusions": "有效",
      "repro_assets": {"code_url": [], "dataset_urls": [], "project_page": []},
      "reproducibility_notes": "未开源",
      "confidence": {"research_goal": "high"},
      "unverified_claims": ["假设 batch size 为 32"],
      "extra_field": "应当保留与否均可"
    }
    ```"""
    return reply, {"prompt": 100, "completion": 50, "model": "fake"}


def test_extract_experiment_offline():
    md = "## Abstract\nWe study DDI. Code: https://github.com/foo/bar\n## Methods\nGNN."
    result = extract_experiment(md, chat_fn=fake_chat_ok)
    assert result["research_goal"] == "预测药物相互作用"
    assert result["metrics"][0]["value"] == "0.95"
    # 正则兜底：LLM 没给 code_url，但原文里有
    assert result["repro_assets"]["code_url"] == ["https://github.com/foo/bar"]
    assert result["unverified_claims"] == ["假设 batch size 为 32"]
    assert result["token_usage"]["prompt"] == 100
    assert "generated_at" in result


def fake_chat_missing_fields(messages, **kw):
    return '{"research_goal": "只有目标"}', {"prompt": 1, "completion": 1, "model": "fake"}


def test_missing_fields_get_empty_defaults():
    result = extract_experiment("abstract", chat_fn=fake_chat_missing_fields)
    assert result["method"] == {"name": "", "architecture": "", "key_components": []}
    assert result["datasets"] == []
    assert result["metrics"] == []
    assert result["repro_assets"]["code_url"] == []


def test_parse_json_reply_variants():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('前言 {"a": {"b": 2}} 后记') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_reply("完全没有 JSON")


def test_generate_report_retries_empty_reply():
    """首次空回复（GLM 思考吃满 max_tokens 的历史缺陷）→ 禁思考重问一次。"""
    calls = []

    def fake_chat(messages, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return "   ", {"prompt": 5, "completion": 0, "model": "m"}
        assert kw.get("thinking") is False  # 重问必须禁用思考
        return "## 一句话总结：内容完整有效的报告。", {"prompt": 7, "completion": 9, "model": "m"}

    report, usage = generate_report("论文文本", chat_fn=fake_chat)
    assert report.startswith("## 一句话总结")
    assert usage == {"prompt": 12, "completion": 9, "model": "m"}
    assert len(calls) == 2


def test_generate_report_raises_on_persistent_empty():
    """两次都空 → 抛 RuntimeError（调用方按分析失败处理，绝不写空报告）。"""
    def fake_chat(messages, **kw):
        return "", {"prompt": 1, "completion": 0}

    with pytest.raises(RuntimeError, match="空报告"):
        generate_report("论文文本", chat_fn=fake_chat)
