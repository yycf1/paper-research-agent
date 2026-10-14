"""Evidence-level diagnostics with versioned labels and explicit human review."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from paper_agent.rag.qa import Retriever, answer_question
from paper_agent.rag.tracing import fingerprint, hit_record
from paper_agent.rag.vectorstore import Hit


def corpus_fingerprint(hits: list[Hit]) -> str:
    rows = [
        {key: row[key] for key in ("paper_id", "chunk_id", "title", "section", "page", "text_sha256")}
        for row in (hit_record(h) for h in hits)
    ]
    return fingerprint(sorted(rows, key=lambda r: (r["paper_id"], r["chunk_id"])))


def load_snapshot(path: Path) -> list[Hit]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("hits"), list):
        raise ValueError("Snapshot requires schema_version=1 and a hits list")
    if any(not isinstance(row, dict) for row in data["hits"]):
        raise ValueError("Snapshot hits must be objects")
    allowed = {f.name for f in fields(Hit)}
    hits = [Hit(**{k: v for k, v in row.items() if k in allowed}) for row in data["hits"]]
    identities = [(h.paper_id, h.chunk_id) for h in hits]
    if any(not h.paper_id or not h.chunk_id or not isinstance(h.text, str) for h in hits):
        raise ValueError("Snapshot hits require paper_id, chunk_id and text")
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate snapshot chunk identities")
    if data.get("corpus_sha256") != corpus_fingerprint(hits):
        raise ValueError("Snapshot fingerprint does not match its content")
    return hits


def write_snapshot(path: Path, hits: list[Hit]) -> None:
    data = {"schema_version": 1, "corpus_sha256": corpus_fingerprint(hits), "hits": [hit_record(h) for h in hits]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cases(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or not isinstance(data.get("cases"), list)
        or not data["cases"]
    ):
        raise ValueError("Dataset requires schema_version=1 and nonempty cases")
    ids = set()
    for case in data["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Cases must be objects")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in ids or not isinstance(case.get("question"), str):
            raise ValueError("Each case needs a unique id and a question")
        ids.add(cid)
        if not case["question"].strip() or case.get("annotation_status") not in {"text_checked", "pending"}:
            raise ValueError(f"Invalid question or annotation status: {cid}")
        if case.get("should_answer") is not None and not isinstance(case["should_answer"], bool):
            raise ValueError(f"Invalid answer expectation: {cid}")
        history = case.get("history", [])
        if not isinstance(history, list) or any(
            not isinstance(turn, list) or len(turn) != 2 or not all(isinstance(t, str) for t in turn)
            for turn in history
        ):
            raise ValueError(f"Invalid history: {cid}")
        groups = case.get("evidence_groups", [])
        if not isinstance(groups, list) or any(not isinstance(g, list) or not g for g in groups):
            raise ValueError(f"Invalid evidence groups: {cid}")
        for group in groups:
            for ref in group:
                if not isinstance(ref, dict) or any(
                    not isinstance(ref.get(k), str) or not ref[k]
                    for k in ("paper_id", "chunk_id", "text_sha256", "quote")
                ):
                    raise ValueError(f"Incomplete evidence reference: {cid}")
        if case["annotation_status"] == "text_checked" and case.get("should_answer") is True and not groups:
            raise ValueError(f"Answerable checked case lacks evidence: {cid}")
    return data


def load_reviews(path: Path | None) -> dict:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("Review file requires a reviews list")
    out = {}
    for row in data["reviews"]:
        if not isinstance(row, dict):
            raise ValueError("Reviews must be objects")
        cid = row.get("case_id")
        if not cid or cid in out or not row.get("artifact_sha256") or not row.get("reviewer"):
            raise ValueError("Reviews need unique case_id, artifact_sha256 and reviewer")
        for key in ("answer_correct", "citation_supported", "refusal_correct"):
            if row.get(key) is not None and not isinstance(row[key], bool):
                raise ValueError(f"Review {key} must be boolean or null")
        if not any(isinstance(row.get(k), bool) for k in ("answer_correct", "citation_supported", "refusal_correct")):
            raise ValueError("A review needs at least one explicit judgment")
        out[cid] = row
    return out


def _ref_matches(ref: dict, row: dict) -> bool:
    return (
        row["paper_id"] == ref["paper_id"]
        and row["chunk_id"] == ref["chunk_id"]
        and row["text_sha256"] == ref["text_sha256"]
        and ref["quote"] in row["text"]
    )


def _group_matches(group, rows):
    return any(_ref_matches(ref, row) for ref in group for row in rows)


def _artifact_fingerprint(case, trace, corpus_sha):
    retrieval = trace.get("retrieval", {})
    final = retrieval.get("stages", {}).get("final", [])
    answer = trace.get("answer")
    context = trace.get("context", final if answer is not None else [])
    return fingerprint(
        {
            "case": case,
            "corpus_sha256": corpus_sha,
            "query": retrieval.get("query"),
            "mode": retrieval.get("mode"),
            "final": final,
            "context": context,
            "answer": answer,
        }
    )


def _apply_case_review(row, review):
    row["failure_stage"] = row["automatic_failure_stage"]
    row["review"] = {
        "status": "not_reviewed",
        "answer_correct": None,
        "citation_supported": None,
        "refusal_correct": None,
    }
    if review is not None:
        if row["trace"].get("answer") is None:
            row["review"]["status"] = "answer_not_generated"
        elif review["artifact_sha256"] != row["artifact_sha256"]:
            row["review"]["status"] = "stale_review"
        else:
            row["review"] = {
                "status": "reviewed",
                **{
                    k: review.get(k)
                    for k in ("reviewer", "answer_correct", "citation_supported", "refusal_correct", "note")
                },
            }
            if row["failure_stage"] is None:
                if review.get("citation_supported") is False:
                    row["failure_stage"] = "citation_not_supported"
                elif review.get("answer_correct") is False:
                    row["failure_stage"] = "answer_incorrect"
                elif review.get("refusal_correct") is False:
                    row["failure_stage"] = "refusal_incorrect"
    if row["failure_stage"] is None:
        row["failure_stage"] = "none_observed" if row["review"]["status"] == "reviewed" else "answer_review_pending"


def apply_reviews(report: dict, reviews: dict) -> dict:
    """Review saved outputs without repeating retrieval or model calls."""
    if report.get("schema_version") != 1 or not isinstance(report.get("cases"), list):
        raise ValueError("Unsupported diagnostic report")
    if set(reviews) - {r["id"] for r in report["cases"]}:
        raise ValueError("Review references a case outside this report")
    updated = deepcopy(report)
    for row in updated["cases"]:
        if row["id"] not in reviews:
            continue
        if (
            row.get("corpus_sha256") != updated["corpus_sha256"]
            or "case_definition" not in row
            or "automatic_failure_stage" not in row
        ):
            raise ValueError("Report lacks verifiable review provenance")
        actual = _artifact_fingerprint(row["case_definition"], row["trace"], row["corpus_sha256"])
        if actual != row["artifact_sha256"]:
            raise ValueError("Report output changed after its artifact fingerprint was recorded")
        _apply_case_review(row, reviews[row["id"]])
    updated["source_report_sha256"] = fingerprint(report)
    updated["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    updated["reviewed_answers"] = sum(r["review"]["status"] == "reviewed" for r in updated["cases"])
    updated["failure_counts"] = dict(Counter(r["failure_stage"] for r in updated["cases"]))
    return updated


def diagnose_case(
    case: dict, trace: dict, corpus: list[Hit], *, dataset_corpus_sha: str, corpus_sha: str, review: dict | None = None
) -> dict:
    corpus_rows = [hit_record(h) for h in corpus]
    groups = case.get("evidence_groups", [])
    label_status = case["annotation_status"]
    if label_status == "text_checked":
        if dataset_corpus_sha != corpus_sha:
            label_status = "corpus_changed"
        elif any(not all(_group_matches([ref], corpus_rows) for ref in group) for group in groups):
            label_status = "invalid_evidence"
    retrieval = trace.get("retrieval", {})
    stages = retrieval.get("stages", {})
    final = stages.get("final", [])
    context = trace.get("context", final if trace.get("answer") is not None else [])
    answer = trace.get("answer")
    artifact_sha = _artifact_fingerprint(case, trace, corpus_sha)
    row = {
        "id": case["id"],
        "question": case["question"],
        "kind": case.get("kind", "unspecified"),
        "annotation_status": label_status,
        "expected_facts": case.get("expected_facts", []),
        "artifact_sha256": artifact_sha,
        "case_definition": deepcopy(case),
        "corpus_sha256": corpus_sha,
        "trace": trace,
        "metrics": None,
        "failure_stage": None,
        "review": {
            "status": "not_reviewed",
            "answer_correct": None,
            "citation_supported": None,
            "refusal_correct": None,
        },
        "citation_check": None,
    }
    if trace.get("error_type"):
        row["failure_stage"] = "execution_error"
    elif label_status != "text_checked":
        row["failure_stage"] = "labels_unavailable"
    elif groups:
        matched = [_group_matches(g, final) for g in groups]
        rank = next((i for i, hit in enumerate(final, 1) if any(_group_matches(g, [hit]) for g in groups)), None)
        row["metrics"] = {
            "hit_at_k": any(matched),
            "all_groups_at_k": all(matched),
            "label_evidence_recall_at_k": sum(matched) / len(groups),
            "rr": 1 / rank if rank else 0.0,
            "first_evidence_rank": rank,
        }
        pool = stages.get("dense", []) + stages.get("sparse", [])
        statuses = retrieval.get("stage_status", {})
        if not all(matched):
            if not all(_group_matches(g, pool) for g in groups):
                row["failure_stage"] = "candidate_recall_miss"
            elif statuses.get("fused") == "ok" and not all(_group_matches(g, stages["fused"]) for g in groups):
                row["failure_stage"] = "fusion_cut"
            else:
                row["failure_stage"] = "rerank_or_topk_cut"
        elif answer is not None and not all(_group_matches(g, context) for g in groups):
            row["failure_stage"] = "evidence_rejected_before_generation"
    if answer is not None:
        refs = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
        invalid = sorted({n for n in refs if n < 1 or n > len(context)})
        row["citation_check"] = {
            "numbers": refs,
            "invalid_numbers": invalid,
            "has_citation": bool(refs),
            "semantic_support": "not_automatically_judged",
        }
        if row["failure_stage"] is None and invalid:
            row["failure_stage"] = "invalid_citation_number"
        elif row["failure_stage"] is None and case.get("should_answer") is True and not refs:
            row["failure_stage"] = "citation_missing"
    row["automatic_failure_stage"] = row["failure_stage"]
    _apply_case_review(row, review)
    return row


def run_diagnostics(
    dataset: dict,
    corpus: list[Hit],
    retriever: Retriever,
    *,
    k=8,
    with_answers=False,
    chat_fn=None,
    reviews=None,
    limit=None,
) -> dict:
    corpus_sha = corpus_fingerprint(corpus)
    rows = []
    cases = dataset["cases"][:limit] if limit is not None else dataset["cases"]
    for case in cases:
        started = time.perf_counter()
        trace = {"question": case["question"], "answer": None, "calls": []}
        try:
            if with_answers:
                kwargs = {"chat_fn": chat_fn} if chat_fn is not None else {}
                answer_question(
                    case["question"],
                    retriever=retriever,
                    k=k,
                    paper_id=case.get("paper_id"),
                    history=case.get("history"),
                    trace=trace,
                    **kwargs,
                )
            else:
                query = case.get("retrieval_query") or case["question"]
                trace["query_source"] = "provided_query" if case.get("retrieval_query") else "original_question"
                trace["history_rewrite_evaluated"] = False
                retriever.retrieve(query, k=k, paper_id=case.get("paper_id"), trace=trace.setdefault("retrieval", {}))
        except Exception as exc:
            # Provider exception bodies can contain secrets. Persist only the exception type.
            trace["error_type"] = type(exc).__name__
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        rows.append(
            diagnose_case(
                case,
                trace,
                corpus,
                dataset_corpus_sha=dataset.get("corpus_sha256", ""),
                corpus_sha=corpus_sha,
                review=(reviews or {}).get(case["id"]),
            )
        )
    scored = [r["metrics"] for r in rows if r["metrics"] is not None]
    average = (
        {
            key: sum(float(m[key]) for m in scored) / len(scored)
            for key in ("hit_at_k", "all_groups_at_k", "label_evidence_recall_at_k", "rr")
        }
        if scored
        else {}
    )
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": fingerprint(dataset),
        "corpus_sha256": corpus_sha,
        "corpus_chunks": len(corpus),
        "matches_dataset_corpus": corpus_sha == dataset.get("corpus_sha256"),
        "scope": dataset.get("scope", "development_diagnostics"),
        "k": k,
        "with_answers": with_answers,
        "cases_run": len(rows),
        "cases_scored": len(scored),
        "retrieval_metrics": average,
        "failure_counts": dict(Counter(r["failure_stage"] for r in rows)),
        "reviewed_answers": sum(r["review"]["status"] == "reviewed" for r in rows),
        "limitations": [
            "Evidence labels are a checked subset, not exhaustive relevance judgments.",
            "Missing labeled evidence does not establish that all supporting passages were missed.",
            "Citation numbering checks do not establish semantic support.",
            "Unreviewed answers have no factual accuracy score.",
            "Token usage covers reported chat calls, not embedding or reranking charges.",
        ],
        "cases": rows,
    }


def write_report(path: Path, report: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = path.with_suffix(".md")

    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    labels = {
        "text_checked": "已核对解析文本",
        "pending": "待标注",
        "corpus_changed": "语料版本变化",
        "invalid_evidence": "证据标签失效",
    }
    diagnoses = {
        "candidate_recall_miss": "候选中缺少标注证据",
        "fusion_cut": "融合截断了标注证据",
        "rerank_or_topk_cut": "重排/Top-k 后缺少标注证据",
        "labels_unavailable": "缺少有效标签，不评分",
        "execution_error": "执行失败",
        "evidence_rejected_before_generation": "证据在生成前被拒绝",
        "invalid_citation_number": "引用编号越界",
        "citation_missing": "答案缺少编号引用",
        "citation_not_supported": "复核发现引用不支持结论",
        "answer_incorrect": "复核发现事实错误",
        "refusal_incorrect": "复核发现拒答不当",
        "none_observed": "已复核维度未发现问题",
        "answer_review_pending": "答案尚未生成或复核",
    }
    lines = [
        "# 证据级诊断报告",
        "",
        f"运行：{report['checked_at']}；语料块：{report['corpus_chunks']}。",
        f"完成 {report['cases_run']} 题，检索评分 {report['cases_scored']} 题，答案人工复核 {report['reviewed_answers']} 题。",
        f"模式：{report.get('run_configuration', {}).get('mode', '见 JSON')}；生成答案：{'是' if report['with_answers'] else '否'}。",
        f"语料版本：`{report['corpus_sha256']}`。",
        "",
        "本报告区分检索、引用编号与人工事实复核；未复核不等于正确或错误。",
        "标签只覆盖已核对片段；未命中标注证据不等于没有命中其他支持片段。种子集由助手核对解析文本，不是独立盲测。",
        "",
        "| 问题 | 标签状态 | 标注证据覆盖 | 首个证据排名 | 诊断 | 答案复核 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["cases"]:
        metrics = row["metrics"] or {}
        lines.append(
            "| "
            + " | ".join(
                cell(v)
                for v in (
                    row["question"],
                    labels.get(row["annotation_status"], row["annotation_status"]),
                    metrics.get("label_evidence_recall_at_k", "未评分"),
                    metrics.get("first_evidence_rank", "—"),
                    diagnoses.get(row["failure_stage"], row["failure_stage"]),
                    "未生成" if row["trace"].get("answer") is None else row["review"]["status"],
                )
            )
            + " |"
        )
    lines += ["", "## 使用限制", ""] + [f"- {text}" for text in report["limitations"]]
    lines += ["", "逐阶段候选、原文与版本摘要见同名 JSON。BM25 离线运行不证明向量检索、重排序或模型回答效果。"]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path
