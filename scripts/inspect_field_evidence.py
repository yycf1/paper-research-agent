"""Save a local field-binding snapshot; never call an embedding or language model."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from paper_agent import paths
from paper_agent.config import load_config
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.services.facts import load_bound_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-id", action="append", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON path; a sibling Markdown report is also written",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    markdown = args.output.with_suffix(".md")
    if args.output.suffix.lower() != ".json":
        parser.error("--output must end in .json")
    if not args.overwrite and (args.output.exists() or markdown.exists()):
        parser.error("Output already exists; choose a new path or pass --overwrite")
    cfg = load_config()
    store = VectorStore(paths.vector_dir(cfg))
    lib = paths.library(cfg)
    papers = []
    try:
        for sid in dict.fromkeys(args.paper_id):
            paper = lib.get(sid)
            if paper is None:
                parser.error(f"Unknown local paper: {sid}")
            bound = load_bound_fields(sid, cfg, store.all_hits(paper_id=sid))
            papers.append(
                {"paper_id": sid, "title": paper.paper.title, "field_evidence": bound}
            )
    finally:
        lib.close()
    statuses = Counter(
        f["support_status"]
        for p in papers
        for fs in p["field_evidence"]["fields"].values()
        for f in fs
    )
    citations = Counter(
        f["citation_status"]
        for p in papers
        for fs in p["field_evidence"]["fields"].values()
        for f in fs
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "offline_local_index",
        "model_calls": 0,
        "scope": "Existing extraction and indexed text; no independent PDF or human verification performed by this script.",
        "support_status_counts": dict(statuses),
        "citation_status_counts": dict(citations),
        "papers": papers,
    }
    lines = [
        "# 字段绑定离线快照",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "本报告读取现有抽取与索引，不重新抽取，不调用模型。候选引用可定位不等于事实得到支持；未进行独立人工/PDF复核。",
        "",
        f"支持状态：`{dict(statuses)}`；引用状态：`{dict(citations)}`。统计仅覆盖本次预算内展示的字段，不是准确率。",
        "",
    ]
    for paper in papers:
        bound = paper["field_evidence"]
        lines += [
            f"## {paper['title']}",
            "",
            f"论文：`{paper['paper_id']}`；索引版本：`{bound['source_version']}`。",
            "",
        ]
        for field, facts in bound["fields"].items():
            for fact in facts:
                value = json.dumps(fact["normalized_value"], ensure_ascii=False)
                lines += [
                    f"- **{field}**：{value}；`{fact['citation_status']}` / `{fact['support_status']}`。"
                ]
                for ref in fact["evidence"]:
                    quote = " ".join(ref["quote"].split())
                    lines += [
                        f"  - {ref['section']} / 页 {ref['page']} / `{ref['chunk_id']}`：{quote}"
                    ]
        lines += [
            "",
            f"预算省略：`{bound['omitted']}`；未匹配复核：{bound['unmatched_reviews']}。",
            "",
            "任务、划分、指标口径和单位未核对，不能直接排名。",
            "",
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "papers": len(papers),
                "support": dict(statuses),
                "citations": dict(citations),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
