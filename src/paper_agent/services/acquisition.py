"""仅下载明确选择的论文；不解析、不调用模型，不扫描文献库补下载。"""
from __future__ import annotations

from pathlib import Path

from paper_agent import paths
from paper_agent.download import download_pdf
from paper_agent.library import Library
from paper_agent.services.locks import paper_lock
from paper_agent.services.library_ops import validate_document, UnsupportedFormat

_ACTIONS = {
    "no_link": "可给此记录上传 PDF/DOCX，或重新检索以补充来源链接",
    "authorization": "来源要求授权；请通过合法访问渠道取得全文后上传到此记录",
    "access_denied": "来源拒绝访问；可稍后重试，或手动取得全文后上传到此记录",
    "not_found": "全文地址失效；可重新检索补充链接，或上传全文",
    "rate_limited": "来源暂时限流，请等待后重试未完成项",
    "network": "检查网络或代理后重试未完成项",
    "non_pdf": "来源返回网页或验证页；请取得真实 PDF 后上传到此记录",
    "invalid_pdf": "下载内容损坏或不可读取；请更换全文文件",
    "metadata_unavailable": "开放副本查询不可用；若为 OpenAlex，请检查 API Key 与额度后重试",
    "local_io": "检查本地存储空间及目录权限后重试",
}


def download_selected_papers(ids: list[str], cfg: dict, lib: Library, result: dict) -> dict:
    items = [{"source_id": sid, "title": "", "status": "pending", "detail": "等待下载"}
             for sid in dict.fromkeys(ids)]

    def publish() -> None:
        result.update({"total": len(items), "completed": sum(row["status"] not in {"pending", "running"} for row in items),
                       "items": [dict(row) for row in items]})

    publish()
    for item in items:
        with paper_lock(lib, item["source_id"]):
            sid = item["source_id"]
            record = lib.get(sid)
            if record is None:
                item.update(status="missing", detail="文献信息已被移除，未下载")
            else:
                item["title"] = record.paper.title
                cached = bool(record.pdf_path and Path(record.pdf_path).is_file())
                if cached:
                    try:
                        validate_document(Path(record.pdf_path))
                    except UnsupportedFormat:
                        cached = False
                if cached:
                    item.update(status="already_saved", detail="本地已有全文，跳过重复下载")
                elif not record.paper.pdf_urls and record.paper.source not in {"openalex", "arxiv"} and not record.paper.doi:
                    item.update(status="no_link", failure_kind="no_link", detail=_ACTIONS["no_link"])
                else:
                    item.update(status="running", detail="正在获取全文，可能尝试其他开放副本")
                    publish()
                    try:
                        saved = download_pdf(record.paper, paths.papers_dir(cfg), proxy=cfg.get("proxy", "") or "")
                        current = lib.get(sid)
                        if current is None:
                            item.update(status="missing", detail="下载期间文献信息已被移除，未恢复记录")
                        else:
                            lib.set_status(sid, "downloaded", pdf_path=str(saved), error=None)
                            item.update(status="saved", detail="全文已保存；尚未自动解析或分析")
                    except Exception as exc:
                        code = getattr(exc, "code", "download_failed")
                        detail = str(exc)[:1500]
                        current = lib.get(sid)
                        if current:
                            status = "download_failed" if current.status in {"discovered", "download_failed"} else current.status
                            lib.set_status(sid, status, error=detail)
                        item.update(status="no_link" if code == "no_link" else "failed", failure_kind=code,
                                    detail=detail, action=_ACTIONS.get(code, "可重试未完成项，或给此记录上传全文"),
                                    attempts=getattr(exc, "attempts", [])[-12:])
        publish()
    return {}
