"""Content contracts for local artifacts and retrieval chunks; never store keys."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from paper_agent.rag import splitter

EXPERIMENT_CONTRACT = "experiment-v3-metric-conditions"
REPORT_CONTRACT = "report-v2-segments"


def parser_contract() -> str:
    from paper_agent.parsing.pdf import MAX_PAGES

    return f"parse-v2-appendix-{MAX_PAGES}"


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def digest(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def model_identity(cfg: dict, kind: str) -> dict:
    options = cfg.get(kind, {})
    return {key: options.get(key, "") for key in ("base_url", "model")}


def artifact_input(markdown: str, cfg: dict, contract: str) -> str:
    model = None if contract.startswith("parse-") else model_identity(cfg, "llm")
    return digest({"source": digest(markdown), "model": model, "contract": contract})


def artifact_current(path: Path, markdown: str, cfg: dict, contract: str) -> bool:
    try:
        manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text(encoding="utf-8"))
        return (manifest["input_sha256"] == artifact_input(markdown, cfg, contract)
                and manifest["artifact_sha256"] == digest(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_artifact(path: Path, text: str, markdown: str, cfg: dict, contract: str) -> None:
    _atomic_text(path, text)
    manifest = {"input_sha256": artifact_input(markdown, cfg, contract), "artifact_sha256": digest(text),
                "source_sha256": digest(markdown), "contract": contract}
    _atomic_text(path.with_name(path.name + ".manifest.json"), json.dumps(manifest, indent=2))


def index_metadata(markdown: str, chunks, cfg: dict, mode: str) -> list[dict]:
    source_sha = digest(markdown)
    embedding_sha = digest(model_identity(cfg, "embedding"))
    signature = digest({"source": source_sha, "embedding": embedding_sha, "mode": mode,
                        "splitter": [1, splitter.TARGET_TOKENS, splitter.MAX_TOKENS, splitter.OVERLAP_TOKENS],
                        "chunks": [{"text": c.text, **c.metadata} for c in chunks]})
    return [{**c.metadata, "mode": mode, "source_sha256": source_sha, "index_fingerprint": signature,
             "embedding_fingerprint": embedding_sha, "expected_chunks": len(chunks)} for c in chunks]


def index_current(store, sid: str, metas: list[dict]) -> bool:
    checker = getattr(store, "index_current", None)
    return bool(metas and callable(checker) and checker(sid, metas[0]["index_fingerprint"], len(metas)))


def knowledge_current(directory: Path, markdown: str, cfg: dict) -> bool:
    return (artifact_current(directory / "experiment.json", markdown, cfg, EXPERIMENT_CONTRACT)
            and artifact_current(directory / "report.md", markdown, cfg, REPORT_CONTRACT))
