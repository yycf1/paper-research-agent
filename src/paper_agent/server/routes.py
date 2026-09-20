"""REST API 路由：/api/*。只做参数解析、状态码映射与 JSON 序列化。"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from paper_agent import __version__, paths
from paper_agent.chatstore import ChatStore
from paper_agent.config import load_config
from paper_agent.download import safe_dirname
from paper_agent.graph.pipeline import run_pipeline
from paper_agent.library import Library, PaperRecord
from paper_agent.models import Paper
from paper_agent.rag.qa import Retriever, answer_question, sources_from_hits
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.services.assistant import handle_message, suggest_directions
from paper_agent.services.library_ops import UnsupportedFormat, delete_paper, import_local_file
from paper_agent.services.process import process_paper
from paper_agent.services.repro import generate_repro_plan
from paper_agent.services.search import PreviewResult, ingest_papers, search_sources
from paper_agent.server import jobs

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


# ---------------------------------------------------------------------------
# 依赖：每请求独立打开账本连接
# ---------------------------------------------------------------------------


def open_lib() -> Library:
    lib = paths.library(load_config())
    try:
        yield lib
    finally:
        lib.close()


def _retriever() -> Retriever:
    return Retriever(VectorStore(paths.vector_dir(load_config())))


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def paper_to_dict(p: Paper) -> dict:
    return {
        "source_id": p.source_id,
        "source": p.source,
        "title": p.title,
        "authors": p.authors,
        "year": p.year,
        "doi": p.doi,
        "citations": p.citations,
        "has_pdf": bool(p.pdf_urls),
        "pdf_urls": p.pdf_urls,
        "landing_page": p.landing_page,
        "abstract_only": p.abstract_only,
    }


def record_to_dict(rec: PaperRecord, *, with_abstract: bool = False) -> dict:
    out = paper_to_dict(rec.paper)
    out["abstract"] = rec.paper.abstract if with_abstract else ""
    out.update({"status": rec.status, "error": rec.error, "added_at": rec.added_at, "tag": rec.tag})
    return out


# ---------------------------------------------------------------------------
# 库浏览
# ---------------------------------------------------------------------------


@router.get("/status")
def api_status() -> dict:
    cfg = load_config()
    lib = paths.library(cfg)
    try:
        counts = lib.counts()
    finally:
        lib.close()
    chunks = 0
    try:
        chunks = VectorStore(paths.vector_dir(cfg)).count()
    except Exception:
        pass  # 向量库未初始化（首次使用）不视为错误
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "vector_chunks": chunks,
        "version": __version__,
    }


@router.get("/papers")
def api_papers(
    status: str | None = Query(None, description="按状态过滤"),
    tag: str | None = Query(None, description="按分组标签过滤"),
    untagged: bool = Query(False, description="只看未分组论文"),
    q: str | None = Query(None, description="标题/作者/DOI 模糊匹配"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    lib: Library = Depends(open_lib),
) -> dict:
    records = lib.list(status=status, tag=tag)
    if untagged:
        records = [r for r in records if not r.tag]
    if q:
        needle = q.lower()
        records = [
            r for r in records
            if needle in r.paper.title.lower()
            or needle in (r.paper.doi or "").lower()
            or any(needle in a.lower() for a in r.paper.authors)
        ]
    return {
        "total": len(records),
        "papers": [record_to_dict(r) for r in records[offset: offset + limit]],
    }


@router.get("/tags")
def api_tags(lib: Library = Depends(open_lib)) -> dict:
    """分组列表（含主动创建的空分组）→ [{name, count}]，外加数据源统计与未分组数量。"""
    by_source: dict[str, int] = {}
    untagged = 0
    for r in lib.list():
        by_source[r.paper.source] = by_source.get(r.paper.source, 0) + 1
        if not r.tag:
            untagged += 1
    return {"tags": lib.groups(), "sources": by_source, "untagged": untagged}


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/tags", status_code=201)
def api_create_tag(req: TagCreate, lib: Library = Depends(open_lib)) -> dict:
    """主动创建空分组（先建组、后归类的用法）。已存在返回 409。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "分组名不能为空")
    if not lib.create_group(name):
        raise HTTPException(409, f"分组已存在：{name}")
    return {"name": name, "count": 0}


class TagRename(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.patch("/tags/{name}")
def api_rename_tag(name: str, req: TagRename, lib: Library = Depends(open_lib)) -> dict:
    """重命名分组，组内论文标签同步更新。"""
    new_name = req.name.strip()
    names = {g["name"] for g in lib.groups()}
    if name not in names:
        raise HTTPException(404, f"分组不存在：{name}")
    if new_name == name:
        count = next(g["count"] for g in lib.groups() if g["name"] == name)
        return {"name": name, "count": count}
    if new_name in names:
        raise HTTPException(409, f"目标分组已存在：{new_name}")
    moved = lib.rename_group(name, new_name)
    return {"name": new_name, "count": moved}


@router.delete("/tags/{name}")
def api_delete_tag(name: str, lib: Library = Depends(open_lib)) -> dict:
    """删除分组：组内论文全部回到「未分组」（论文本身不删）。"""
    if name not in {g["name"] for g in lib.groups()}:
        raise HTTPException(404, f"分组不存在：{name}")
    moved = lib.delete_group(name)
    return {"ok": True, "ungrouped": moved}


@router.get("/papers/{sid}")
def api_paper_detail(sid: str, lib: Library = Depends(open_lib)) -> dict:
    sid = _resolve_sid(sid, lib)  # 允许片段匹配（与 CLI 一致的容错），歧义 400
    rec = lib.get(sid)
    assert rec is not None

    cfg = load_config()
    out = record_to_dict(rec, with_abstract=True)

    knowledge = paths.knowledge_dir(cfg) / safe_dirname(rec.paper.source_id)
    experiment_path = knowledge / "experiment.json"
    report_path = knowledge / "report.md"
    repro_plan_path = knowledge / "repro_plan.md"
    fulltext_path = paths.parsed_dir(cfg) / safe_dirname(rec.paper.source_id) / "full_text.md"
    out["experiment"] = (
        json.loads(experiment_path.read_text(encoding="utf-8"))
        if experiment_path.exists() else None
    )
    out["report"] = (
        report_path.read_text(encoding="utf-8") if report_path.exists() else None
    )
    out["repro_plan"] = (
        repro_plan_path.read_text(encoding="utf-8") if repro_plan_path.exists() else None
    )
    out["full_text"] = (
        fulltext_path.read_text(encoding="utf-8") if fulltext_path.exists() else None
    )
    return out


# ---------------------------------------------------------------------------
# 检索 / 问答 / 对话
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    sources: str = "openalex,europepmc,arxiv"
    max_results: int = Field(10, ge=1, le=50)
    year_from: int | None = Field(None, ge=1900, le=2100)
    raw: bool = False
    # 高级检索条件（M13）
    field: str = Field("all", pattern="^(all|title|abstract)$")
    author: str = Field("", max_length=120)
    sort: str = Field("relevance", pattern="^(relevance|citations|newest)$")
    min_citations: int | None = Field(None, ge=0)
    oa_only: bool = False
    year_to: int | None = Field(None, ge=1900, le=2100)


@router.post("/search")
def api_search(req: SearchRequest, lib: Library = Depends(open_lib)) -> dict:
    """检索预览：不写库，返回候选清单（已在库中的带 duplicate_of 标记）。"""
    cfg = load_config()
    try:
        preview = search_sources(
            req.query, lib=lib, cfg=cfg, sources=req.sources,
            max_results=req.max_results, year_from=req.year_from, raw=req.raw,
            field=req.field, author=req.author or None, sort=req.sort,
            min_citations=req.min_citations, oa_only=req.oa_only, year_to=req.year_to,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:  # 缺 Key 等环境问题
        raise HTTPException(503, str(exc)) from exc
    return _preview_to_dict(preview)


def _preview_to_dict(preview: PreviewResult) -> dict:
    return {
        "candidates": [
            {
                **paper_to_dict(c.paper),
                "abstract": c.paper.abstract,
                "duplicate_of": c.duplicate_of,
            }
            for c in preview.candidates
        ],
        "total_hits": preview.total_hits,
        "notes": preview.notes,
        "source_hits": preview.source_hits,
        "topics": preview.plan.get("topics", []),
        "optimized": preview.plan.get("optimized", False),
    }


class PaperIn(BaseModel):
    source: str
    source_id: str
    title: str
    authors: list[str] = []
    abstract: str = ""
    year: int | None = None
    doi: str | None = None
    citations: int | None = None
    pdf_urls: list[str] = []
    landing_page: str | None = None
    abstract_only: bool = False


class IngestRequest(BaseModel):
    papers: list[PaperIn] = Field(min_length=1, max_length=200)
    tag: str = Field("", max_length=60)


@router.post("/papers/ingest")
def api_ingest(req: IngestRequest, lib: Library = Depends(open_lib)) -> dict:
    """入库预览时选中的候选；重复项幂等合并（不重复计数为新论文）。"""
    papers = [
        Paper(
            source=p.source, source_id=p.source_id, title=p.title, authors=p.authors,
            abstract=p.abstract, year=p.year, doi=p.doi, citations=p.citations,
            pdf_urls=p.pdf_urls, landing_page=p.landing_page, abstract_only=p.abstract_only,
        )
        for p in req.papers
    ]
    result = ingest_papers(papers, lib=lib, tag=req.tag or None)
    return {
        "ingested": len(result.new_papers),
        "duplicates": result.duplicates,
        "papers": [record_to_dict(r) for r in result.new_papers],
    }


class PaperTagUpdate(BaseModel):
    tag: str | None = Field(None, max_length=60)


def _resolve_sid(sid: str, lib: Library) -> str:
    """id 片段解析（与详情/删除一致）：唯一匹配返回全 id，歧义 400，无匹配 404。"""
    if lib.get(sid) is not None:
        return sid
    matches = lib.find(sid)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(400, f"「{sid}」匹配多条：{', '.join(matches)}")
    raise HTTPException(404, f"未找到论文：{sid}")


@router.patch("/papers/{sid}")
def api_set_paper_tag(sid: str, req: PaperTagUpdate, lib: Library = Depends(open_lib)) -> dict:
    """主动归类：设置/清除单篇论文分组（tag 为 null 或空串清除）。"""
    sid = _resolve_sid(sid, lib)
    lib.set_tag(sid, req.tag)
    rec = lib.get(sid)
    assert rec is not None
    return record_to_dict(rec, with_abstract=True)


class BatchTagRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=200)
    tag: str | None = Field(None, max_length=60)


@router.post("/papers/batch-tag")
def api_batch_tag(req: BatchTagRequest, lib: Library = Depends(open_lib)) -> dict:
    """批量归类/移出分组（列表多选操作）。"""
    out = lib.set_tag_many(req.source_ids, req.tag)
    return {"updated": out["updated"], "missing": out["missing"], "tag": (req.tag or "").strip()}


@router.post("/papers/{sid}/plan")
def api_plan_paper(sid: str, lib: Library = Depends(open_lib)) -> dict:
    """生成复现方案（repro_plan.md）：须已分析；同步调用，LLM 通常 10~40s。"""
    sid = _resolve_sid(sid, lib)
    cfg = load_config()
    try:
        plan, usage = generate_repro_plan(sid, cfg=cfg, lib=lib)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:  # 空回复 / 缺 Key
        raise HTTPException(503, str(exc)) from exc
    return {
        "source_id": sid,
        "repro_plan": plan,
        "tokens": usage.get("prompt", 0) + usage.get("completion", 0),
    }


@router.delete("/papers/{sid}")
def api_delete_paper(sid: str, lib: Library = Depends(open_lib)) -> dict:
    """全链删除：账本 + 向量块 + 原文/解析/知识产物。"""
    sid = _resolve_sid(sid, lib)
    ok = delete_paper(sid, cfg=load_config(), lib=lib)
    return {"ok": ok, "source_id": sid}


@router.post("/papers/upload", status_code=201)
def api_upload_paper(
    file: UploadFile = File(...),
    lib: Library = Depends(open_lib),
) -> dict:
    """上传本地 PDF/DOCX：内容哈希幂等，状态直达 downloaded（可再一键处理）。

    每次上传落在自己的临时子目录（防同名并发互覆、失败清理不伤他文件），
    导入完成即清理——import_local_file 已把内容复制进 papers/{sid}/。
    """
    cfg = load_config()
    name = file.filename or "document.pdf"
    upload_dir = paths.data_dir(cfg) / "uploads" / uuid.uuid4().hex[:8]
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(name).name
    try:
        size = 0
        with dest.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "文件超过 50MB 上限")
                out.write(chunk)
        try:
            rec, is_new = import_local_file(dest, cfg=cfg, lib=lib)
        except UnsupportedFormat as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"is_new": is_new, "paper": record_to_dict(rec, with_abstract=True)}
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    paper_id: str | None = None
    k: int = Field(8, ge=1, le=20)


@router.post("/ask")
def api_ask(req: AskRequest) -> dict:
    retriever = _retriever()
    if retriever.store.count() == 0:
        raise HTTPException(400, "向量库为空：先检索并处理论文建立索引")
    try:
        answer, hits = answer_question(
            req.question, retriever=retriever, k=req.k, paper_id=req.paper_id
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"answer": answer, "sources": sources_from_hits(hits)}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


@router.post("/chat")
def api_chat(req: ChatRequest, lib: Library = Depends(open_lib)) -> dict:
    cfg = load_config()
    p = cfg.get("pipeline", {}) or {}
    params = {
        "max_per_source": 10,
        "year_from": p.get("year_from"),
        "top_n": p.get("top_n", 5),
        "download_budget": p.get("download_budget", 10),
    }
    store = ChatStore(paths.data_dir(cfg) / "library.db")
    try:
        sid = req.session_id
        if sid is None:
            sid = store.create_session(title=req.message)
        elif not any(s["id"] == sid for s in store.list_sessions()):
            raise HTTPException(404, f"未找到会话：{sid}（可能已删除）")
        prior = store.get_messages(sid)
        store.append_message(sid, role="user", content=req.message)
        # 近几轮问答作为语境传给问答链（指代消解），取相邻的 (user, assistant) 对
        history = [
            (prior[i]["content"], prior[i + 1]["content"])
            for i in range(len(prior) - 1)
            if prior[i]["role"] == "user" and prior[i + 1]["role"] == "assistant"
        ][-3:]

        reply = handle_message(
            req.message, lib=lib, retriever=_retriever(), params=params, cfg=cfg,
            history=history,
        )
        store.append_message(
            sid, role="assistant", content=reply.reply, intent=reply.intent,
            understanding=reply.understanding, notes=reply.notes,
            sources=reply.sources,
        )
        return {
            "session_id": sid,
            "intent": reply.intent,
            "reply": reply.reply,
            "understanding": reply.understanding,
            "notes": reply.notes,
            "new_papers": [record_to_dict(r) for r in reply.new_papers],
            "sources": reply.sources,
        }
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------


@router.get("/sessions")
def api_sessions() -> dict:
    store = _chat_store()
    try:
        return {"sessions": store.list_sessions()}
    finally:
        store.close()


class SessionCreate(BaseModel):
    title: str = Field("新对话", max_length=60)


@router.post("/sessions", status_code=201)
def api_create_session(req: SessionCreate) -> dict:
    store = _chat_store()
    try:
        sid = store.create_session(title=req.title)
        return {"id": sid, "title": req.title[:30], "message_count": 0}
    finally:
        store.close()


@router.get("/sessions/{sid}/messages")
def api_session_messages(sid: str) -> dict:
    store = _chat_store()
    try:
        if not any(s["id"] == sid for s in store.list_sessions()):
            raise HTTPException(404, f"未找到会话：{sid}")
        return {"session_id": sid, "messages": store.get_messages(sid)}
    finally:
        store.close()


@router.delete("/sessions/{sid}")
def api_delete_session(sid: str) -> dict:
    store = _chat_store()
    try:
        if not store.delete_session(sid):
            raise HTTPException(404, f"未找到会话：{sid}")
        return {"ok": True}
    finally:
        store.close()


def _chat_store() -> ChatStore:
    return ChatStore(paths.data_dir(load_config()) / "library.db")


@router.get("/explore")
def api_explore(lib: Library = Depends(open_lib)) -> dict:
    return {"directions": suggest_directions(lib)}


# ---------------------------------------------------------------------------
# 长任务：流水线 / 单篇处理
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_n: int | None = Field(None, ge=1, le=50)
    year_from: int | None = Field(None, ge=1900, le=2100)
    budget: int | None = Field(None, ge=1, le=100)
    max_per_source: int = Field(10, ge=1, le=50)


@router.post("/jobs/run", status_code=202)
def api_run_job(req: RunRequest) -> dict:
    cfg = load_config()
    p = cfg.get("pipeline", {}) or {}
    params = {
        "max_per_source": req.max_per_source,
        "top_n": req.top_n if req.top_n is not None else p.get("top_n", 5),
        "year_from": req.year_from if req.year_from is not None else p.get("year_from"),
        "download_budget": req.budget if req.budget is not None else p.get("download_budget", 10),
    }

    def _run(_result: dict) -> dict:
        state = run_pipeline(req.query, params)
        return {"stats": state["stats"], "notes": state["notes"], "selected": state["selected"]}

    job = jobs.submit("run_pipeline", _run, result={"query": req.query, "params": params})
    return job.to_dict()


@router.post("/papers/{sid}/process", status_code=202)
def api_process_paper(sid: str, lib: Library = Depends(open_lib)) -> dict:
    if lib.get(sid) is None:
        raise HTTPException(404, f"未找到论文：{sid}")

    def _run(_result: dict) -> dict:
        cfg = load_config()
        job_lib = paths.library(cfg)  # 任务线程用自己的连接
        try:
            return process_paper(sid, cfg, job_lib)
        finally:
            job_lib.close()

    job = jobs.submit("process_paper", _run, result={"source_id": sid})
    return job.to_dict()


@router.get("/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"未找到任务：{job_id}（或服务已重启）")
    return job.to_dict()


@router.get("/jobs")
def api_jobs() -> dict:
    return {"jobs": [j.to_dict() for j in jobs.list_jobs()]}
