"""复现规划器（二期第一个模块）：experiment.json + 复现资产 → repro_plan.md。

对应 PLAN.md §13「复现规划器」与 §4 的硬约束：二期的全部输入收敛到
experiment.json 和复现资产链接。本模块只做规划（读什么、装什么、跑什么、
对齐什么），不执行任何代码——环境搭建与代码执行属后续模块。

产物与 experiment.json / report.md 同目录（data/knowledge/{sid}/repro_plan.md），
不改动五态生命周期状态机：复现方案是增量产物，不是流程阶段。
"""

from __future__ import annotations

import json

from paper_agent import paths
from paper_agent.download import safe_dirname
from paper_agent.extraction.report import chat_markdown_safe
from paper_agent.library import Library
from paper_agent.llm import chat

MAX_TEXT_CHARS = 20_000  # 论文节选上限：方案以 experiment.json 为主输入，节选只补细节

_PROMPT = """你是论文复现规划助手。依据给出的结构化实验知识与论文节选，产出一份可执行的复现方案（Markdown）。

结构（严格按以下小节，标题用 `## `）：
1. `## 复现目标`——一句话：复现什么实验、对齐哪个指标；
2. `## 复现资产评估`——代码仓库是否可得、数据集是否公开、还缺什么；
3. `## 环境搭建`——语言/框架、关键依赖与版本建议、硬件需求（GPU/显存/内存）；
4. `## 实施步骤`——编号步骤：获取代码 → 环境搭建 → 数据准备 → 训练/推理 → 评估，每步一句要点；
5. `## 预期指标与比对`——论文报告的指标值，复现结果如何与之比对；
6. `## 风险与缺口`——细节不足处、可能的坑、文中未说明的信息。

要求：
- 只依据输入内容，不编造；没有的信息直接写「文中未说明」；
- 缺少代码或数据链接只能写“公开情况未确认”，不得推断“未公开/不公开/不可获取”。已有链接也只是候选，未实测不得声称可访问；
- 每条硬件、版本、实现或训练参数建议必须标为“建议（非原文事实）”；没有依据的指标不得补写；
- 「未验证声明」里的推断是 LLM 补全的，不要当作论文事实引用；
- 步骤克制、可执行，总长度 500～900 字。

结构化实验知识（experiment.json）：
---
{experiment}
---

论文节选（可能被截断）：
---
{paper_text}
---"""


def _load_experiment(cfg: dict, sid: str) -> dict:
    path = paths.knowledge_dir(cfg) / safe_dirname(sid) / "experiment.json"
    if not path.exists():
        raise ValueError("尚未生成实验知识（experiment.json）：先运行 pa analyze 或「一键处理」")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"experiment.json 损坏（{exc}）：重新 analyze 后再试") from exc
    if not isinstance(data, dict):
        raise ValueError("experiment.json 内容不是 JSON 对象")
    return data


def repro_plan_path(cfg: dict, sid: str):
    return paths.knowledge_dir(cfg) / safe_dirname(sid) / "repro_plan.md"


def generate_repro_plan(sid: str, *, cfg: dict, lib: Library, chat_fn=chat) -> tuple[str, dict]:
    """为已分析论文生成复现方案，写盘并返回 (markdown, token用量)。

    论文须已分析（experiment.json 存在）；解析文本可选（增强细节）。幂等：
    重跑覆盖旧方案，由调用方决定是否需要 --force 提示。
    """
    rec = lib.get(sid)
    if rec is None:
        raise ValueError(f"未找到论文：{sid}")
    experiment = _load_experiment(cfg, sid)

    md_path = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
    paper_text = ""
    if md_path.exists():
        try:
            paper_text = md_path.read_text(encoding="utf-8")[:MAX_TEXT_CHARS]
        except OSError:
            paper_text = ""

    prompt = _PROMPT.format(
        experiment=json.dumps(experiment, ensure_ascii=False, indent=2),
        paper_text=paper_text or "（无解析文本，仅依据实验知识）",
    )
    plan, usage = chat_markdown_safe(
        [{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.2,
        chat_fn=chat_fn,
    )
    # Reject the observed unsupported asset claim instead of persisting it as fact.
    assets = experiment.get("repro_assets") or {}
    missing_assets = not assets.get("code_url") or not assets.get("dataset_urls")
    if missing_assets:
        import re
        pattern = r"(?:代码|数据集|数据|code|dataset)[^。\n]{0,32}(?:未公开|不公开|不开放|没有公开|均未开放|not\s+(?:public|available))"
        negative = re.search(pattern, plan, re.I)
        if negative and negative.group(0) not in paper_text:
            plan, repair_usage = chat_markdown_safe([
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": plan},
                {"role": "user", "content": "上一版无依据地断言代码或数据未公开。请重写全文：缺失链接一律写公开情况未确认；建议标为建议，不得断言未公开。"},
            ], max_tokens=2048, temperature=0.1, chat_fn=chat_fn)
            usage = {**usage, **{key: usage.get(key, 0) + repair_usage.get(key, 0) for key in ("prompt", "completion")}}
            if re.search(pattern, plan, re.I):
                raise RuntimeError("复现方案仍将缺失信息写成未公开，未保存该方案；请核对资产后重试")
    out = repro_plan_path(cfg, sid)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(plan, encoding="utf-8")
    return plan, usage
