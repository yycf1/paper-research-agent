"""Opt-in public-OA download check; writes isolated files, never opens the user's ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

from paper_agent.download import download_pdf
from paper_agent.models import Paper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow real HTTP requests and PDF downloads")
    parser.add_argument("--output-root", type=Path, default=Path("data/download-live"))
    args = parser.parse_args()
    if not args.live:
        parser.error("This check uses real HTTP requests; pass --live to run it")
    target = args.output_root.resolve() / uuid.uuid4().hex
    cases = [
        ("W3217045679", "https://dl.acm.org/doi/pdf/10.1145/3501815"),
        ("W4382998948", "https://hull-repository.worktribe.com/preview/4537002/IEEE%20TMI__LViT-2023_AccptedVer2.pdf"),
        ("W2905224888", "https://arxiv.org/pdf/1812.08434"),
        ("W4385572149", "https://aclanthology.org/2023.nlrse-1.7.pdf"),
    ]
    target.mkdir(parents=True)
    results = []
    for work_id, url in cases:
        started = time.monotonic()
        result = {"work_id": f"openalex:{work_id}"}
        try:
            paper = Paper(source="openalex", source_id=result["work_id"], title=work_id, pdf_urls=[url])
            path = download_pdf(paper, target)
            with pymupdf.open(path) as doc:
                pages, chars = doc.page_count, len(doc[0].get_text())
            result.update(result="downloaded_and_parsed", bytes=path.stat().st_size, pages=pages,
                          first_page_text_chars=chars, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        except Exception as exc:
            result.update(result="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
        result["seconds"] = round(time.monotonic() - started, 2)
        results.append(result)
        report = {"date": datetime.now(UTC).isoformat(), "personal_library_modified": False,
                  "model_calls": 0, "results": results}
        (target / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        time.sleep(3)
    return int(any(result["result"] != "downloaded_and_parsed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
