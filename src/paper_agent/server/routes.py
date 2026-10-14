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
from paper_agent.config import (
    load_config,
    load_runtime_settings,
    resolve_endpoint,
    settings_path,
)
from paper_agent.download import safe_dirname
from paper_agent.graph.pipeline import run_pipeline
from paper_agent.library import Library, PaperRecord
from paper_agent.llm import test_endpoint
from paper_agent.models import Paper
from paper_agent.rag.qa import Retriever, answer_question, sources_from_hits
from paper_agent.rag.vectorstore import VectorStore
from paper_agent.server import agent_jobs, jobs
from paper_agent.services.assistant import handle_message, suggest_directions
from paper_agent.services.facts import load_bound_fields
from paper_agent.services.library_ops import (
    UnsupportedFormat,
    delete_paper,
    import_local_file,
)
from paper_agent.services.process import process_paper
from paper_agent.services.repro import generate_repro_plan
from paper_agent.services.search import PreviewResult, ingest_papers, search_sources

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


@router.get("/papers/{sid}/facts")
def api_paper_facts(sid: str, lib: Library = Depends(open_lib)) -> dict:
    rec = lib.get(sid)
    if rec is None:
        raise HTTPException(404, "论文不存在")
    cfg = load_config()
    store = VectorStore(paths.vector_dir(cfg))
    result = load_bound_fields(sid, cfg, store.all_hits(paper_id=sid))
    return {"title": rec.paper.title, **result}


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
    from paper_agent.rag.splitter import chunk_abstract, split_markdown
    from paper_agent.services.versions import (
        index_current,
        index_metadata,
        knowledge_current,
    )

    store = VectorStore(paths.vector_dir(cfg))
    if callable(getattr(store, "all_hits", None)):
        hits = store.all_hits(paper_id=sid)
        text = out["full_text"] if out["full_text"] is not None else rec.paper.abstract
        mode = "fulltext" if out["full_text"] is not None else "abstract"
        chunks = split_markdown(text, sid, rec.paper.title) if mode == "fulltext" else chunk_abstract(sid, rec.paper.title, text)
        current = index_current(store, sid, index_metadata(text, chunks, cfg, mode))
        registered = any(getattr(h, "source_sha256", "") for h in hits)
        state = "ready" if current else "stale" if registered else "legacy" if hits else "unindexed"
        out["index_state"] = {"status": state, "chunk_count": len(hits), "mode": mode,
                              "can_ask": state in {"ready", "legacy"}}
    else:
        out["index_state"] = {"status": "unknown", "chunk_count": None, "can_ask": False}
    out["knowledge_current"] = bool(out["full_text"] and knowledge_current(knowledge, out["full_text"], cfg))
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


_REPRO_VIEW_EXTS = (".py", ".md", ".txt", ".json", ".log")
_REPRO_FILE_MAX_BYTES = 200_000  # 文件查看上限：工作区是代码/日志，超限截断


def _repro_base(cfg: dict, sid: str) -> Path:
    return paths.data_dir(cfg) / "repro" / safe_dirname(sid)


@router.get("/papers/{sid}/repro")
def api_paper_repro(sid: str, lib: Library = Depends(open_lib)) -> dict:
    """复现产物概览：方案 + 工作区文件列表 + 运行记录（M24 浏览器可见化）。"""
    sid = _resolve_sid(sid, lib)
    cfg = load_config()
    base = _repro_base(cfg, sid)
    ws = base / "workspace"
    files: list[dict] = []
    if ws.is_dir():
        for p in ws.iterdir():
            if p.is_file():
                st = p.stat()
                files.append({"name": p.name, "bytes": st.st_size, "mtime": st.st_mtime})
        files.sort(key=lambda x: x["mtime"], reverse=True)
    runs: list[dict] = []
    runs_path = base / "runs.jsonl"
    if runs_path.exists():
        for line in runs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except ValueError:
                continue
        runs.reverse()  # 新→旧
    plan_path = paths.knowledge_dir(cfg) / safe_dirname(sid) / "repro_plan.md"
    plan = plan_path.read_text(encoding="utf-8") if plan_path.exists() else None
    return {"source_id": sid, "plan": plan, "files": files, "runs": runs}


@router.get("/papers/{sid}/repro/file")
def api_paper_repro_file(sid: str, name: str = Query(""), lib: Library = Depends(open_lib)) -> dict:
    """查看工作区文件内容：白名单与执行器同一套（纯文件名 + 扩展名限制）。"""
    sid = _resolve_sid(sid, lib)
    name = str(name or "").strip()
    if not name or Path(name).name != name:
        raise HTTPException(400, "文件名非法（只允许纯文件名）")
    if not name.endswith(_REPRO_VIEW_EXTS):
        raise HTTPException(400, "只允许查看 .py/.md/.txt/.json/.log 文件")
    path = _repro_base(load_config(), sid) / "workspace" / name
    if not path.is_file():
        raise HTTPException(404, f"文件不存在：{name}")
    data = path.read_bytes()[:_REPRO_FILE_MAX_BYTES]
    return {"name": name, "content": data.decode("utf-8", errors="replace")}


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
    paper_ids: list[str] | None = Field(None, min_length=1, max_length=6)
    k: int = Field(8, ge=1, le=20)


@router.post("/ask")
def api_ask(req: AskRequest, lib: Library = Depends(open_lib)) -> dict:
    from paper_agent.agent.scope import PaperScope

    try:
        ids = PaperScope(lib).query_ids(req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    scope = {"paper_ids": list(ids)} if ids is not None else {}
    retriever = _retriever()
    if retriever.store.count() == 0:
        raise HTTPException(400, "向量库为空：先检索并处理论文建立索引")
    trace = {}
    try:
        answer, hits = answer_question(
            req.question, retriever=retriever, k=req.k, trace=trace, **scope
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"answer": answer, "sources": sources_from_hits(hits),
            "answer_status": trace.get("answer_status"), "citation_check": trace.get("citation_check"),
            "retrieval_notes": trace.get("retrieval", {}).get("notes", [])}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class CompareRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    paper_ids: list[str] = Field(min_length=2, max_length=6)
    k_per_paper: int = Field(3, ge=1, le=5)


@router.post("/compare")
def api_compare(req: CompareRequest, lib: Library = Depends(open_lib)) -> dict:
    from paper_agent.agent.executor import ToolExecutor
    from paper_agent.agent.scope import PaperScope

    try:
        scope = PaperScope(lib, allowed_paper_ids=req.paper_ids)
        if len(scope.allowed) < 2:
            raise ValueError("比较需要至少两篇不同论文")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    executor = ToolExecutor(lib=lib, retriever=_retriever(), cfg=load_config(),
                            allowed_paper_ids=list(scope.allowed))
    result = executor.execute("compare_papers", req.model_dump_json())
    if not result.ok:
        raise HTTPException(400 if result.status == "fatal" else 503, result.error)
    return result.data


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
# 运行时设置（M17）：模型厂商 / Key 在 Web 端可配，落 data/settings.json
# ---------------------------------------------------------------------------


class SettingsPayload(BaseModel):
    """base_url/model 传空串=清空覆盖（回退 config.yaml）；api_key=None/空=保持已存 Key。

    GET 永远只回脱敏后的 Key（尾 4 位），不回显明文——密钥一旦展示过就会
    进日志/截图/缓存，界面只需让用户确认「配过了」而不是看见它。
    """

    base_url: str = Field("", max_length=300)
    model: str = Field("", max_length=120)
    api_key: str | None = Field(None, max_length=300)
    theme: str | None = Field(None, pattern="^(dark|light)$")


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


@router.get("/settings")
def api_get_settings() -> dict:
    cfg = load_config()
    ep = resolve_endpoint(cfg.get("llm", {}) or {})
    return {
        "base_url": ep.base_url,
        "model": ep.model,
        "api_key_masked": _mask_key(ep.api_key),
        "has_key": bool(ep.api_key),
        "theme": load_runtime_settings(cfg).get("theme", "dark"),
    }


@router.put("/settings")
def api_save_settings(req: SettingsPayload) -> dict:
    cfg = load_config()
    path = settings_path(cfg)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}  # 损坏文件直接重建
    data["base_url"] = req.base_url.strip()
    data["model"] = req.model.strip()
    if req.api_key and req.api_key.strip():  # 留空=不改已存 Key
        data["api_key"] = req.api_key.strip()
    if req.theme in ("dark", "light"):
        data["theme"] = req.theme
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ep = resolve_endpoint(load_config().get("llm", {}) or {})
    return {"ok": True, "api_key_masked": _mask_key(ep.api_key), "has_key": bool(ep.api_key)}


@router.post("/settings/test")
def api_test_settings(req: SettingsPayload) -> dict:
    """测试连接：优先用表单当前值（允许未保存先试），缺省回退当前生效配置。"""
    ep = resolve_endpoint(load_config().get("llm", {}) or {})
    ok, msg = test_endpoint(
        base_url=req.base_url.strip() or ep.base_url,
        api_key=(req.api_key or "").strip() or ep.api_key,
        model=req.model.strip() or ep.model,
    )
    return {"ok": ok, "message": msg}


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

    def _run(result: dict) -> dict:
        cfg = load_config()
        job_lib = paths.library(cfg)  # 任务线程用自己的连接
        try:

            def _progress(stages: list) -> None:
                # jobs.submit 传给任务函数的就是轮询端点返回的同一个 dict：
                # 原地写入阶段快照，前端轮询即时可见（长任务不再跑完才见步骤）
                result["stages"] = [dict(s) for s in stages]

            return process_paper(sid, cfg, job_lib, progress=_progress)
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


# ---------------------------------------------------------------------------
# Agent 任务（M16）：后台执行 + 步骤轮询 + HITL 确认门
# ---------------------------------------------------------------------------


class AgentTaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    max_steps: int | None = Field(None, ge=1, le=50)
    token_budget: int | None = Field(None, ge=1000)
    anchor_source_id: str | None = Field(None, max_length=100)  # M26：锚定论文（防续任务漂移）
    allowed_paper_ids: list[str] | None = Field(None, min_length=1, max_length=6)


@router.post("/agent/tasks", status_code=202)
def api_agent_submit(req: AgentTaskRequest, lib: Library = Depends(open_lib)) -> dict:
    """提交 Agent 任务：立即返回任务 id，步骤经 SSE / 轮询可见。

    任务 id 预生成并编入轨迹文件名（时间戳-id.jsonl）：注册表在内存里，
    服务重启后 GET /agent/tasks/{id} 与历史列表靠文件名关联回读轨迹。
    anchor_source_id 给出时校验论文存在（404/400 由 _resolve_sid 决断），
    循环层注入锚定约束、执行器收紧工具作用域。
    """
    from datetime import datetime

    anchor = None
    if (req.anchor_source_id or "").strip():
        anchor = _resolve_sid(req.anchor_source_id.strip(), lib)
    from paper_agent.agent.scope import PaperScope

    try:
        scope = PaperScope(lib, anchor=anchor, allowed_paper_ids=req.allowed_paper_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = load_config()
    trace_dir = paths.data_dir(cfg) / "traces" / "web"
    tid = uuid.uuid4().hex[:12]
    trace_path = trace_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{tid}.jsonl"
    state = agent_jobs.registry.submit(
        req.task,
        cfg=cfg,
        max_steps=req.max_steps,
        token_budget=req.token_budget,
        trace_path=trace_path,
        task_id=tid,
        anchor=anchor,
        allowed_paper_ids=list(scope.allowed) if scope.allowed is not None else None,
    )
    return state.to_dict(with_steps=False)


def _trace_task_id(stem: str) -> str:
    """轨迹文件名 → 任务 id：新命名 `时间戳-id.jsonl` 取末段，旧文件用整个 stem。"""
    parts = stem.split("-")
    return parts[-1] if len(parts) >= 3 else stem


def _history_task(path: Path, *, with_steps: bool) -> dict | None:
    """解析一条 web 轨迹为任务视图；无有效记录的空文件返回 None。"""
    task_text = ""
    anchor = ""
    allowed_paper_ids = None
    steps: list[dict] = []
    final_rec: dict | None = None
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                kind = rec.get("type")
                if kind == "task":
                    task_text = rec.get("task", "")
                    anchor = rec.get("anchor", "")
                    allowed_paper_ids = rec.get("allowed_paper_ids")
                elif kind == "step":
                    steps.append(
                        {k: rec.get(k) for k in ("step", "tool", "arguments", "status", "detail", "duration_ms", "comparison")}
                    )
                elif kind == "final":
                    final_rec = rec
    except OSError:
        return None
    if not task_text and not steps and final_rec is None:
        return None
    answer = usage = stopped = ""
    status = "done"
    error = ""
    if final_rec:
        answer = final_rec.get("answer", "")
        usage = final_rec.get("usage", {"prompt": 0, "completion": 0})
        stopped = final_rec.get("stopped_reason", "")
        if stopped == "error":
            status, error = "error", answer
    else:
        # 逐步追加写的设计：缺 final 记录即进程中断，如实标注而不是伪装完成
        status, stopped, error = "error", "error", "轨迹缺少最终记录（服务中断）"
    out = {
        "id": _trace_task_id(path.stem),
        "task": task_text,
        "anchor": anchor,
        "allowed_paper_ids": allowed_paper_ids,
        "status": status,
        "step_count": len(steps),
        "answer": answer,
        "stopped_reason": stopped,
        "usage": usage,
        "error": error,
        "pending_confirm": None,
        "ts": path.stat().st_mtime,
    }
    if with_steps:
        out["steps"] = steps
    return out


@router.get("/agent/tasks")
def api_agent_tasks() -> dict:
    """任务历史：内存注册表（含进行中）+ 磁盘轨迹回读，按 id 去重，新→旧。"""
    cfg = load_config()
    out: list[dict] = []
    seen: set[str] = set()
    for s in agent_jobs.registry.list_tasks():
        d = s.to_dict(with_steps=False)
        d["live"] = True
        d["ts"] = s.started_at
        out.append(d)
        seen.add(s.id)
    trace_dir = paths.data_dir(cfg) / "traces" / "web"
    if trace_dir.is_dir():
        files = sorted(trace_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            d = _history_task(p, with_steps=False)
            if d and d["id"] not in seen:
                d["live"] = False
                out.append(d)
    return {"tasks": out}


@router.get("/agent/tasks/{task_id}")
def api_agent_task(task_id: str) -> dict:
    state = agent_jobs.registry.get(task_id)
    if state is not None:
        d = state.to_dict()
        d["live"] = True
        return d
    # 服务重启后注册表清空：按 id 回读轨迹（含全部步骤，前端时间线原样重建）
    cfg = load_config()
    trace_dir = paths.data_dir(cfg) / "traces" / "web"
    if trace_dir.is_dir():
        for p in trace_dir.glob("*.jsonl"):
            if _trace_task_id(p.stem) == task_id:
                d = _history_task(p, with_steps=True)
                if d is not None:
                    d["live"] = False
                    return d
    raise HTTPException(404, f"未找到 Agent 任务：{task_id}（或服务已重启且无轨迹）")


@router.get("/agent/tasks/{task_id}/events")
async def api_agent_events(task_id: str):
    """SSE 步骤事件流：替代 1.2s 轮询——同进程读共享状态对象，增量推送。"""
    import asyncio

    state = agent_jobs.registry.get(task_id)
    if state is None:
        raise HTTPException(404, f"未找到 Agent 任务：{task_id}（或服务已重启）")

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    async def gen():
        yield _sse({"type": "snapshot", **state.to_dict()})
        sent = len(state.steps)
        last_status = state.status
        while True:
            if len(state.steps) > sent:
                for st in state.steps[sent:]:
                    yield _sse({"type": "step", "step": st})
                sent = len(state.steps)
            if state.status != last_status:
                last_status = state.status
                yield _sse({"type": "status", "state": state.to_dict(with_steps=False)})
            if state.status in ("done", "error"):
                break
            await asyncio.sleep(0.4)
        yield _sse({"type": "end"})

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AgentConfirmRequest(BaseModel):
    approve: bool


@router.post("/agent/tasks/{task_id}/confirm")
def api_agent_confirm(task_id: str, req: AgentConfirmRequest) -> dict:
    """对等待中的确认门决断（放行/拒绝）；任务不处于等待态返回 409。"""
    ok = agent_jobs.registry.decide(task_id, req.approve)
    if not ok:
        raise HTTPException(409, "任务不在等待确认状态（可能已完成、超时或 id 不存在）")
    return {"ok": True}
