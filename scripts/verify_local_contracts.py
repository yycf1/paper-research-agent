"""Offline API integration using real SQLite/Chroma and an injected answer model."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from paper_agent import paths
from paper_agent.download import safe_dirname
from paper_agent.models import Paper
from paper_agent.rag.qa import Retriever, answer_question
from paper_agent.rag.splitter import split_markdown
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.server import routes
from paper_agent.server.app import create_app
from paper_agent.services.versions import (
    EXPERIMENT_CONTRACT,
    REPORT_CONTRACT,
    index_metadata,
    save_artifact,
)

directory = Path("data/local-contract-check") / uuid.uuid4().hex[:12]
cfg = {"data_dir": str(directory.resolve()), "retrieval": {"mode": "bm25"},
       "embedding": {"model": "manual-offline-vector"}}
library = paths.library(cfg)
store = VectorStore(paths.vector_dir(cfg))
ids = ["local:contract-a", "local:contract-b"]
for sid in ids:
    paper = Paper(source="local", source_id=sid, title="Original contract example " + sid)
    library.upsert_paper(paper)
    markdown = "## Experiments\nSynthetic-A uses a fixed test split. Recall@10 is 0.5 as a ratio."
    parsed = paths.parsed_dir(cfg) / safe_dirname(sid) / "full_text.md"
    parsed.parent.mkdir(parents=True, exist_ok=True)
    parsed.write_text(markdown, encoding="utf-8")
    chunks = split_markdown(markdown, sid, paper.title)
    store.upsert_paper_chunks(sid, [chunk.text for chunk in chunks],
                              index_metadata(markdown, chunks, cfg, "fulltext"), [[1.0, 0.0] for _ in chunks])
    knowledge = paths.knowledge_dir(cfg) / safe_dirname(sid)
    experiment = {"research_goal": "Inspect synthetic evaluation", "datasets": [{"name": "Synthetic-A"}],
                  "metrics": [{"name": "Recall@10", "value": "0.5"}]}
    save_artifact(knowledge / "experiment.json", json.dumps(experiment), markdown, cfg, EXPERIMENT_CONTRACT)
    save_artifact(knowledge / "report.md", "Original offline report.", markdown, cfg, REPORT_CONTRACT)
    library.set_status(sid, "indexed")
library.close()
routes.load_config = lambda: cfg
routes._retriever = lambda: Retriever(store, cfg=cfg)
model_calls = []


def fake_chat(messages, **kwargs):
    model_calls.append(1)
    return "使用 Synthetic-A 的固定测试划分 [1]。", {}


def offline_answer(*args, **kwargs):
    return answer_question(*args, **kwargs, chat_fn=fake_chat)


routes.answer_question = offline_answer
checks = []
with TestClient(create_app()) as client:
    home = client.get("/")
    assert home.status_code == 200
    for asset in re.findall(r'(?:src|href)="(/assets/[^"\s]+)"', home.text):
        assert client.get(asset).status_code == 200
    checks.append("packaged_frontend_assets")
    paper = client.get("/api/papers/" + ids[0]).json()
    assert paper["index_state"]["status"] == "ready" and paper["knowledge_current"] is True
    checks.append("real_index_readiness")
    comparison = client.post("/api/compare", json={"topic": "Synthetic-A Recall", "paper_ids": ids})
    assert comparison.status_code == 200
    assert [row["paper_id"] for row in comparison.json()["papers"]] == ids
    assert comparison.json()["comparability"]["ranking_allowed"] is False
    assert model_calls == []
    checks.append("scoped_comparison_without_answer_model")
    answer = client.post("/api/ask", json={"question": "Synthetic-A split", "paper_ids": ids})
    assert answer.status_code == 200 and answer.json()["answer_status"] == "generated"
    assert answer.json()["citation_check"]["status"] == "passed"
    assert all(source["paper_id"] in ids for source in answer.json()["sources"])
    checks.append("real_retrieval_with_scripted_answer_and_citations")
    assert client.post("/api/compare", json={"topic": "q", "paper_ids": [ids[0], "local:missing"]}).status_code == 400
    checks.append("unknown_scope_rejected")
print(json.dumps({"checks": checks, "scripted_model_calls": len(model_calls), "real_model_calls": 0,
                  "storage": "new ignored synthetic fixture directory"}, ensure_ascii=False))
