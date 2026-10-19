"""对指定开发集做组件消融；产物保存在忽略目录，不能自动覆盖历史公开指标。"""
import argparse
import copy
import json
from pathlib import Path
from datetime import datetime, timezone

from paper_agent import paths
from paper_agent.config import load_config
from paper_agent.rag.embedder import embed
from paper_agent.rag.eval import load_evalset, rank_metrics, aggregate_rank_metrics
from paper_agent.rag.hybrid import bm25
from paper_agent.rag.qa import Retriever
from paper_agent.rag.vectorstore import VectorStore

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dataset", type=Path, required=True)
parser.add_argument("--output", type=Path, default=root / "data/evidence-eval/retrieval-benchmark.json")
args = parser.parse_args()
cfg = load_config()
store = VectorStore(paths.vector_dir(cfg))
cases = load_evalset(args.dataset)["cases"]
rows = {name: [] for name in ("vector", "bm25", "hybrid", "hybrid_rerank")}
notes = []
for i, case in enumerate(cases, 1):
    if not case.get("expect_sections"):
        continue
    question, pid = case["question"], case.get("paper_id")
    vector = embed([question], cfg=cfg)
    corpus = store.all_hits(paper_id=pid)
    for mode in rows:
        if mode == "bm25":
            hits = bm25(question, corpus, 8)
        else:
            options = copy.deepcopy(cfg)
            options["retrieval"]["hybrid"] = mode != "vector"
            options["reranker"]["enabled"] = mode == "hybrid_rerank"
            retriever = Retriever(store, embed_fn=lambda texts: vector, cfg=options)
            hits = retriever.retrieve(question, k=8, paper_id=pid)
            notes.extend({"case": case["id"], "mode": mode, "note": n} for n in retriever.notes)
        rows[mode].append({"id": case["id"], **rank_metrics(case, hits)})
    print(f"{i}/{len(cases)} {case['id']}", flush=True)
report = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "dataset": str(args.dataset), "total_cases": len(cases),
    "ranking_cases": len(rows["vector"]), "corpus_chunks": store.count(),
    "scope": "期望章节子串匹配；按评测集 paper_id 限定检索。非独立盲测，不代表通用效果。",
    "metrics": {mode: aggregate_rank_metrics(data) for mode, data in rows.items()},
    "fallbacks": notes, "details": rows,
}
out = args.output
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report["metrics"], ensure_ascii=False, indent=2), flush=True)
