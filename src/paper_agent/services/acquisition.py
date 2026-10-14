"""仅下载明确选择的论文；不解析、不调用模型，不扫描文献库补下载。"""
from __future__ import annotations

from pathlib import Path

from paper_agent import paths
from paper_agent.download import download_pdf
from paper_agent.library import Library


def download_selected_papers(ids: list[str], cfg: dict, lib: Library, result: dict) -> dict:
    items = [{"source_id": sid, "title": "", "status": "pending", "detail": "等待下载"}
             for sid in dict.fromkeys(ids)]

    def publish() -> None:
        result.update({"total": len(items), "completed": sum(row["status"] not in {"pending", "running"} for row in items),
                       "items": [dict(row) for row in items]})

    publish()
    for item in items:
        sid = item["source_id"]
        record = lib.get(sid)
        if record is None:
            item.update(status="missing", detail="文献信息已被移除，未下载")
        else:
            item["title"] = record.paper.title
            if record.pdf_path and Path(record.pdf_path).is_file():
                item.update(status="already_saved", detail="本地已有全文，跳过重复下载")
            elif not record.paper.pdf_urls:
                item.update(status="no_link", detail="来源没有提供全文候选链接，已保留文献信息；可导入本地文件")
            else:
                item.update(status="running", detail="正在获取全文，可能尝试其他开放副本")
                publish()
                try:
                    saved = download_pdf(record.paper, paths.papers_dir(cfg), proxy=cfg.get("proxy", "") or "")
                    current = lib.get(sid)
                    if current is None:
                        item.update(status="missing", detail="下载期间文献信息已被移除，未恢复记录")
                    else:
                        status = "downloaded" if current.status in {"discovered", "download_failed"} else current.status
                        lib.set_status(sid, status, pdf_path=str(saved), error=None)
                        item.update(status="saved", detail="全文已保存；尚未自动解析或分析")
                except Exception as exc:
                    current = lib.get(sid)
                    if current:
                        status = "download_failed" if current.status in {"discovered", "download_failed"} else current.status
                        lib.set_status(sid, status, error=str(exc)[:500])
                    item.update(status="failed", detail=str(exc)[:500])
        publish()
    return {}
