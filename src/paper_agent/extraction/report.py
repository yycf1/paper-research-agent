"""精读报告生成：解析后的论文文本 → report.md（人读优先，面向「读懂」而非「评审」）。"""

from __future__ import annotations

import json
import re

from paper_agent.extraction.coverage import add_usage, coverage_note, document_windows
from paper_agent.llm import chat

MAX_INPUT_CHARS = 36_000

_PROMPT = """你是论文精读助手。依据我提供的论文文本写一份中文精读报告，用 Markdown 输出。

结构（严格按以下小节，标题用 `## `）：
1. `## 一句话总结`——不超过 60 字；
2. `## 研究问题与动机`——要解决什么问题、为什么现有方法不够；
3. `## 方法脉络`——按「输入→核心思路→关键设计→输出」讲清楚，面向读懂而非评审；
4. `## 实验设计`——数据集、基线、指标、关键设置；
5. `## 主要结论与局限`——作者的主张与你自己观察到的局限；
6. `## 可复现性备注`——代码/数据是否公开（原文出现的链接原样保留）、细节充分度、复现的主要风险点。

要求：
- 只依据原文，不编造；原文没写的直接说「文中未说明」；
- 保留原文中的具体数字（指标值、数据规模等）；
- 总长度 600～1000 字，克制、信息密度优先。

论文文本（可能被截断）：
---
{paper_text}
---"""


_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n?```\s*$", re.DOTALL)


class EmptyReportError(RuntimeError):
    """LLM 重试后仍返回空报告（按单篇分析失败处理，不中止整批）。"""


def chat_markdown_safe(
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float = 0.3,
    chat_fn=chat,
) -> tuple[str, dict]:
    """生成 Markdown 产物的通用 LLM 调用：空回复禁思考重问一次，仍空抛错。

    空回复的根因（M9 实测）：GLM 混合推理模型的思考 tokens 吃满 max_tokens。
    问答链、精读报告、复现方案共用此兜底。
    """
    reply, usage = chat_fn(messages, max_tokens=max_tokens, temperature=temperature)
    if not reply.strip():
        reply2, usage2 = chat_fn(
            messages, max_tokens=max_tokens, temperature=temperature, thinking=False
        )
        usage = {
            "prompt": usage.get("prompt", 0) + usage2.get("prompt", 0),
            "completion": usage.get("completion", 0) + usage2.get("completion", 0),
            "model": usage2.get("model") or usage.get("model"),
        }
        reply = reply2
    text = strip_code_fence(reply)
    if not text.strip():
        raise EmptyReportError("LLM 返回空报告（重试后仍为空）")
    return text, usage


def strip_code_fence(text: str) -> str:
    """LLM 有时把整份 Markdown 报告包进代码围栏，剥掉首尾围栏行。"""
    m = _FENCE_RE.match(text.strip())
    return (m.group(1) if m else text).strip() + "\n"


def generate_report(markdown: str, *, chat_fn=chat) -> tuple[str, dict]:
    """生成精读报告。返回 (markdown, token用量)。chat_fn 可注入便于离线测试。

    空回复防护见 chat_markdown_safe——绝不把空报告写盘后还把状态推进到
    analyzed（M11 前的真实缺陷）。
    """
    windows, coverage = document_windows(markdown, segment_chars=MAX_INPUT_CHARS)
    if not windows:
        raise ValueError("解析文本为空，不能生成报告")
    usage = {}
    if len(windows) == 1:
        report, usage = chat_markdown_safe(
            [{"role": "user", "content": _PROMPT.format(paper_text=windows[0]["text"])}],
            max_tokens=2048, temperature=0.3, chat_fn=chat_fn)
    else:
        notes = []
        for i, window in enumerate(windows, 1):
            note, used = chat_markdown_safe([{"role": "user", "content": (
                f"这是论文分段 {i}，字符范围 {window['start']}~{window['end']}，不是全文。"
                "提取本段的研究目标、方法、实验设置、数据划分、指标数值、结果、局限和资产链接；"
                "保留章节、页码标记和数字，未提及的字段留空，不据本段缺失断言全文缺失。\n"
                + window["text"]
            )}], max_tokens=1536, temperature=0.1, chat_fn=chat_fn)
            if len(note) > 6000:
                coverage.setdefault("note_truncated_segments", []).append(i)
            notes.append(f"分段 {i}（{window['start']}~{window['end']}）：\n{note[:6000]}")
            usage = add_usage(usage, used)
        prompt = _PROMPT.format(paper_text="\n\n".join(notes)) + (
            "\n以上是带范围的分段笔记，综合时保留来源范围，不补写未覆盖的事实；"
            "分段冲突明确说明，不能自行选择更好看的数字。\n" + coverage_note(coverage))
        report, used = chat_markdown_safe([{"role": "user", "content": prompt}],
                                          max_tokens=2048, temperature=0.2, chat_fn=chat_fn)
        usage = add_usage(usage, used)
    report += "\n> " + coverage_note(coverage) + "\n"
    report += "\n<!-- analysis-coverage: " + json.dumps(coverage, ensure_ascii=False) + " -->\n"
    return report, usage
