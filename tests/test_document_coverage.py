from __future__ import annotations

import json

import pymupdf
import pytest

from paper_agent.extraction.coverage import document_windows
from paper_agent.extraction.experiment_flow import extract_experiment
from paper_agent.extraction.report import generate_report
from paper_agent.graph import pipeline
from paper_agent.library import Library
from paper_agent.models import Paper
from paper_agent.parsing import readable_chars
from paper_agent.parsing.pdf import extract_markdown


def pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()


@pytest.mark.parametrize("heading", ["Appendix", "Appendix A: Implementation Details", "A Additional Experiments", "Supplementary Material"])
def test_pdf_resumes_after_references(tmp_path, heading):
    path = tmp_path / "paper.pdf"
    pdf(path, ["Introduction\nmain content", "References\nSmith, 2020. Bibliography entry.",
               heading + "\nLATE_APPENDIX_FACT 42"])
    text = extract_markdown(path)
    assert "main content" in text and "LATE_APPENDIX_FACT 42" in text
    assert "Smith, 2020" not in text
    assert "<!-- p.3 -->" in text and f"## {heading}" in text
    assert '"appendix_resumed": true' in text


def test_same_page_appendix_and_no_strip_mode(tmp_path):
    path = tmp_path / "same.pdf"
    pdf(path, ["References\nReference entry.\nAppendix B\nTAIL_FACT 7"])
    assert "TAIL_FACT 7" in extract_markdown(path)
    kept = extract_markdown(path, strip_references=False)
    assert "Reference entry." in kept and "TAIL_FACT 7" in kept


def test_page_limit_is_machine_readable(tmp_path, monkeypatch):
    monkeypatch.setattr("paper_agent.parsing.pdf.MAX_PAGES", 2)
    path = tmp_path / "limited.pdf"
    pdf(path, ["one", "two", "three"])
    text = extract_markdown(path)
    assert "three" not in text and '"truncated": true' in text
    _, coverage = document_windows(text)
    assert coverage["upstream_parse"]["processed_pages"] == 2
    assert coverage["upstream_parse"]["total_pages"] == 3


def test_coverage_metadata_does_not_make_blank_pdf_readable(tmp_path):
    path = tmp_path / "blank.pdf"
    pdf(path, [""])
    text = extract_markdown(path)
    assert "parse-coverage" in text
    assert readable_chars(text) == 0


def test_late_fact_reaches_extraction_and_report():
    text = "## Introduction\n" + "background. " * 3500 + "\n## Appendix\nTAIL_DATASET_42"
    extraction_inputs = []

    def extract_chat(messages, **kw):
        content = messages[0]["content"]
        extraction_inputs.append(content)
        return json.dumps({"datasets": [{"name": "TAIL_DATASET_42"}] if "TAIL_DATASET_42" in content else []}), {"prompt": 1, "completion": 1}

    result = extract_experiment(text, chat_fn=extract_chat)
    assert len(extraction_inputs) == 2
    assert any("TAIL_DATASET_42" in prompt for prompt in extraction_inputs)
    assert result["datasets"] == [{"name": "TAIL_DATASET_42"}]
    assert result["coverage"]["covered_chars"] == len(text)
    assert result["token_usage"]["prompt"] == 2

    report_inputs = []

    def report_chat(messages, **kw):
        content = messages[0]["content"]
        report_inputs.append(content)
        if len(report_inputs) <= 2:
            return ("Appendix: TAIL_DATASET_42" if "TAIL_DATASET_42" in content else "Background note"), {"prompt": 1, "completion": 1}
        assert "TAIL_DATASET_42" in content
        return "## 一句话总结\n使用 TAIL_DATASET_42。", {"prompt": 1, "completion": 1}

    report, usage = generate_report(text, chat_fn=report_chat)
    assert len(report_inputs) == 3 and usage["prompt"] == 3
    assert "TAIL_DATASET_42" in report and "输入覆盖" in report


def test_segment_budget_retains_tail_and_records_omissions():
    text = "opening\n" + "x" * 150 + "\n## Results\nmetric 0.7\n" + "y" * 150 + "\n## Appendix\nlast fact"
    windows, coverage = document_windows(text, segment_chars=80, max_segments=3)
    assert len(windows) == 3 and all(len(w["text"]) <= 80 for w in windows)
    assert "last fact" in windows[-1]["text"]
    assert coverage["status"] == "partial" and coverage["omitted_ranges"]
    assert coverage["covered_chars"] + sum(r["end"] - r["start"] for r in coverage["omitted_ranges"]) == len(text)


def test_segment_conflicts_preserved_as_candidates(monkeypatch):
    monkeypatch.setattr("paper_agent.extraction.experiment_flow.MAX_INPUT_CHARS", 40)
    replies = iter([{"research_goal": "goal A"}, {"research_goal": "goal B"}])
    result = extract_experiment("a" * 60, chat_fn=lambda *a, **kw: (json.dumps(next(replies)), {}))
    assert result["research_goal"] == "goal A"
    assert result["confidence"]["research_goal"] == "low"
    assert any("research_goal" in note for note in result["unverified_claims"])
    assert result["segment_candidates"][1]["fields"]["research_goal"] == "goal B"


def test_long_report_upstream_limit_is_visible(monkeypatch):
    text = '<!-- parse-coverage: {"truncated": true} -->\nshort content'
    report, _ = generate_report(text, chat_fn=lambda *a, **kw: ("## 一句话总结\n内容。", {}))
    assert "部分覆盖" in report


@pytest.mark.parametrize("operation", [extract_experiment, generate_report])
def test_empty_input_fails_without_model(operation):
    with pytest.raises(ValueError, match="为空"):
        operation("", chat_fn=lambda *a, **kw: pytest.fail("空输入不可调用模型"))


def test_raw_file_changes_invalidate_parsed_version(tmp_path, monkeypatch):
    cfg = {"data_dir": str(tmp_path)}
    raw = tmp_path / "source.pdf"
    raw.write_bytes(b"version1")
    sid = "local:source"
    lib = Library(tmp_path / "library.db")
    lib.upsert_paper(Paper(source="local", source_id=sid, title="T"))
    lib.set_status(sid, "downloaded", pdf_path=str(raw))
    calls = []

    def parse(path):
        calls.append(path.read_bytes())
        return "## Method\n" + path.read_text(encoding="utf-8") * 40

    monkeypatch.setattr(pipeline, "extract_markdown", parse)
    try:
        assert pipeline.run_parse(lib, cfg)[0]["parsed"] == 1
        assert pipeline.run_parse(lib, cfg)[0]["parsed"] == 0
        lib.set_status(sid, "indexed")
        raw.write_bytes(b"version2")
        assert pipeline.run_parse(lib, cfg)[0]["parsed"] == 1
        assert calls == [b"version1", b"version2"]
        assert lib.get(sid).status == "parsed"
    finally:
        lib.close()
