"""Bind extracted fields to versioned passages without treating lexical matches as proof."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from paper_agent.rag.hybrid import bm25
from paper_agent.rag.tracing import fingerprint, text_hash

FIELDS = ("research_goal", "datasets", "metrics")


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    text_sha256: str = Field(min_length=1)
    section: str
    page: int | None = Field(default=None, ge=1)
    quote: str = Field(min_length=1, max_length=600)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class BoundFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["research_goal", "datasets", "metrics"]
    raw_value: JsonValue
    normalized_value: JsonValue
    evidence: list[EvidenceReference] = Field(default_factory=list)
    citation_status: Literal["valid", "missing", "invalid"] = "missing"
    support_status: Literal[
        "unknown",
        "unverified",
        "supported",
        "unsupported",
        "conflict",
        "not_applicable",
    ] = "unverified"
    issues: list[str] = Field(default_factory=list)
    claim_sha256: str = ""
    review: dict[str, str] | None = None


class FactReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_sha256: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    review_kind: Literal["human"]
    status: Literal["supported", "unsupported", "conflict", "not_applicable"]
    note: str = Field(min_length=1, max_length=1500)


def source_version(paper_id, hits):
    rows = [
        {
            "paper_id": h.paper_id,
            "chunk_id": getattr(h, "chunk_id", ""),
            "text_sha256": text_hash(h.text),
            "section": h.section,
            "page": h.page,
        }
        for h in hits
        if h.paper_id == paper_id and getattr(h, "chunk_id", "")
    ]
    return fingerprint(sorted(rows, key=lambda row: row["chunk_id"]))


def validate_reference(ref: EvidenceReference, paper_id, hits, version):
    issues = []
    if ref.paper_id != paper_id:
        return ["wrong_paper"]
    matches = [
        h
        for h in hits
        if h.paper_id == paper_id and getattr(h, "chunk_id", "") == ref.chunk_id
    ]
    if len(matches) != 1:
        return ["unknown_or_ambiguous_chunk"]
    hit = matches[0]
    if ref.source_version != version or ref.text_sha256 != text_hash(hit.text):
        issues.append("stale_reference")
    if ref.page != (hit.page or None):
        issues.append("page_mismatch")
    if ref.section != hit.section:
        issues.append("section_mismatch")
    if (
        ref.end > len(hit.text)
        or ref.start >= ref.end
        or hit.text[ref.start : ref.end] != ref.quote
    ):
        issues.append("quote_mismatch")
    return issues


def _normalized(field, raw):
    if field == "research_goal":
        return " ".join(raw.split()) if isinstance(raw, str) and raw.strip() else None
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("name"), str)
        or not raw["name"].strip()
    ):
        return None
    keys = (
        ("name", "source", "split")
        if field == "datasets"
        else ("name", "value", "baseline", "task", "dataset", "split", "unit")
    )
    out = {}
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = " ".join(value.split())
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
        elif value is None:
            out[key] = None
        else:
            return None
    return out


def _occurrence(text, term):
    # Preserve offsets in the original passage. Numeric/identifier fragments must not match longer tokens.
    if not term:
        return None
    pattern = re.escape(term)
    numeric = re.fullmatch(r"\d+(?:\.\d+)?", term) is not None
    if term[0].isalnum():
        pattern = (r"(?<![\w.])" if numeric else r"(?<!\w)") + pattern
    if term[-1].isalnum():
        pattern += r"(?!\w|\.\d)" if numeric else r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE)


def _candidate_refs(field, value, hits, paper_id, version):
    if not hits or value is None:
        return []
    name = value if isinstance(value, str) else str(value["name"])
    # Equal BM25 scores must not change the selected references when index read order changes.
    ranked = bm25(name, sorted(hits, key=lambda h: h.chunk_id), min(8, len(hits)))
    refs = []
    for hit in ranked:
        spans = []
        if field == "datasets":
            match = _occurrence(hit.text, name)
            if not match:
                continue
            spans = [(match.start(), match.end())]
        elif field == "metrics":
            words = [
                w
                for w in re.findall(r"[A-Za-z][A-Za-z0-9@_-]*", name)
                if w.lower() not in {"scores", "score", "metrics", "metric"}
            ]
            names = [m for w in words if (m := _occurrence(hit.text, w))]
            if not names:
                continue
            spans = [(names[0].start(), names[0].end())]
            numbers = re.findall(r"\d+(?:\.\d+)?", str(value.get("value") or ""))
            number_matches = [_occurrence(hit.text, n) for n in numbers]
            if any(m is None for m in number_matches):
                continue
            spans += [(m.start(), m.end()) for m in number_matches if m is not None]
        else:
            anchors = re.search(
                r"\b(?:aim|objective|propose|presents|framework|goal)\b|研究目标|提出",
                hit.text,
                re.IGNORECASE,
            )
            start = (
                anchors.start()
                if anchors
                else min(len(hit.text.split("\n", 1)[0]) + 1, len(hit.text) - 1)
            )
            spans = [(start, min(start + 180, len(hit.text)))]
        start = max(0, min(s for s, _ in spans) - 80)
        end = min(len(hit.text), max(e for _, e in spans) + 160)
        if end - start > 520:
            continue  # Do not truncate away one of the values that selected this candidate.
        ref = EvidenceReference(
            paper_id=paper_id,
            chunk_id=hit.chunk_id,
            source_version=version,
            text_sha256=text_hash(hit.text),
            section=hit.section,
            page=hit.page or None,
            quote=hit.text[start:end],
            start=start,
            end=end,
        )
        refs.append(ref)
        if len(refs) == 2:
            break
    return refs


def bind_fact(field, raw, paper_id, hits, *, proposals=None, review=None):
    version = source_version(paper_id, hits)
    value = _normalized(field, raw)
    fact = BoundFact(
        field=field,
        raw_value=raw,
        normalized_value=value,
        support_status="unknown" if value is None else "unverified",
    )
    if value is None:
        fact.issues.append(
            "missing_value" if raw in (None, "", [], {}) else "invalid_field_type"
        )
    candidates = (
        _candidate_refs(field, value, hits, paper_id, version)
        if proposals is None
        else proposals
    )
    invalid = False
    for candidate in candidates:
        try:
            ref = (
                candidate
                if isinstance(candidate, EvidenceReference)
                else EvidenceReference.model_validate(candidate)
            )
        except ValueError:
            fact.issues.append("invalid_reference_schema")
            invalid = True
            continue
        issues = validate_reference(ref, paper_id, hits, version)
        if issues:
            invalid = True
            fact.issues.extend(issues)
        else:
            fact.evidence.append(ref)
    fact.citation_status = (
        "invalid" if invalid else "valid" if fact.evidence else "missing"
    )
    if value is not None and not fact.evidence:
        fact.issues.append("no_candidate_evidence")
    material = {
        "paper_id": paper_id,
        "source_version": version,
        "field": field,
        "raw_value": raw,
        "normalized_value": value,
        "evidence": [r.model_dump(mode="json") for r in fact.evidence],
        "citation_status": fact.citation_status,
    }
    fact.claim_sha256 = fingerprint(material)
    if review is not None:
        checked = (
            review
            if isinstance(review, FactReview)
            else FactReview.model_validate(review)
        )
        if checked.claim_sha256 != fact.claim_sha256:
            fact.issues.append("stale_review")
        elif checked.status == "supported" and (
            value is None or fact.citation_status != "valid"
        ):
            fact.issues.append("support_requires_valid_evidence")
        else:
            fact.support_status = checked.status
            fact.review = {
                "reviewer": checked.reviewer,
                "review_kind": checked.review_kind,
                "note": checked.note,
            }
    fact.issues = sorted(set(fact.issues))
    return fact


def build_bound_fields(paper_id, structured, hits, *, reviews=(), max_chars=8000):
    scoped = [
        h
        for h in hits
        if getattr(h, "paper_id", None) == paper_id and getattr(h, "chunk_id", "")
    ]
    checked_reviews = [
        r if isinstance(r, FactReview) else FactReview.model_validate(r)
        for r in reviews
    ]
    by_digest = {r.claim_sha256: r for r in checked_reviews}
    if len(by_digest) != len(checked_reviews):
        raise ValueError("Duplicate field reviews")
    output = {field: [] for field in FIELDS}
    omitted = Counter()
    for field in FIELDS:
        raw_field = structured.get(field)
        values = (
            raw_field
            if field != "research_goal" and isinstance(raw_field, list)
            else [raw_field]
        )
        if not values:
            values = [None]
        for raw in values[:8]:
            # Expose oversized/invalid extraction instead of silently clipping it into a different fact.
            if len(json.dumps(raw, ensure_ascii=False)) > 1600:
                fact = bind_fact(field, None, paper_id, scoped)
                fact.issues = ["value_over_budget"]
                omitted[field] += 1
            else:
                fact = bind_fact(field, raw, paper_id, scoped)
                if fact.claim_sha256 in by_digest:
                    fact = bind_fact(
                        field,
                        raw,
                        paper_id,
                        scoped,
                        review=by_digest[fact.claim_sha256],
                    )
            output[field].append(fact.model_dump(mode="json"))
        omitted[field] += max(0, len(values) - 8)
    result = {
        "schema_version": 1,
        "paper_id": paper_id,
        "source_version": source_version(paper_id, scoped),
        "version_basis": "indexed_text_and_metadata",
        "fields": output,
        "omitted": dict(omitted),
        "unmatched_reviews": len(
            set(by_digest) - {f["claim_sha256"] for fs in output.values() for f in fs}
        ),
        "numeric_comparison": {
            "status": "not_assessed",
            "ranking_allowed": False,
            "reason": "任务、数据划分、指标定义和单位尚未逐项核对，不能直接按数值排序。",
        },
        "note": "引用有效只证明片段存在及归属正确；自动匹配是候选依据，语义支持仍需人工核对。",
    }
    # Keep one slot per field and remove extra rows fairly before reducing candidate excerpts.
    while len(json.dumps(result, ensure_ascii=False)) > max_chars:
        longest = max(FIELDS, key=lambda f: len(output[f]))
        if len(output[longest]) > 1:
            output[longest].pop()
            result["omitted"][longest] = result["omitted"].get(longest, 0) + 1
            continue
        evidence_rows = [f for fs in output.values() for f in fs if f["evidence"]]
        if evidence_rows:
            target = max(
                evidence_rows, key=lambda f: len(json.dumps(f, ensure_ascii=False))
            )
            # A changed evidence set invalidates both its digest and any semantic review.
            raw = target["raw_value"]
            replacement = bind_fact(
                target["field"], raw, paper_id, scoped, proposals=[]
            )
            replacement.issues.append("evidence_omitted_by_budget")
            output[target["field"]][0] = replacement.model_dump(mode="json")
            continue
        for field in FIELDS:
            output[field] = [
                bind_fact(field, None, paper_id, scoped).model_dump(mode="json")
            ]
            output[field][0]["issues"] = ["field_omitted_by_budget"]
            result["omitted"][field] = result["omitted"].get(field, 0) + 1
        if len(json.dumps(result, ensure_ascii=False)) > max_chars:
            raise ValueError("Field evidence budget is too small")
        break
    result["unmatched_reviews"] = len(
        set(by_digest) - {f["claim_sha256"] for fs in output.values() for f in fs}
    )
    return result
