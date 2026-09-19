"""版本、自检与库维护命令：version / doctor / prune。"""

from __future__ import annotations

import time

import typer
from rich.table import Table
from openai import OpenAI

from paper_agent import __version__
from paper_agent.cli.core import FAIL, OK, SKIP, WARN, _library, app, console
from paper_agent.config import EndpointConfig, load_config, resolve_endpoint
from paper_agent.graph.hello import run_hello
from paper_agent.services.library_ops import delete_paper
from paper_agent.sources import SourceUnavailable
from paper_agent.tools import SOURCE_MODULES


@app.command()
def version() -> None:
    """显示版本。"""
    console.print(f"paper-agent {__version__}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="开发模式：代码改动自动重启"),
) -> None:
    """启动 Web 服务（REST API + 界面，http://127.0.0.1:8000）。"""
    import uvicorn

    console.print(f"[bold]paper-agent Web 服务[/bold] → http://{host}:{port}")
    console.print("[dim]API 文档 http://%s:%d/docs ｜ Ctrl+C 停止[/dim]" % (host, port))
    if reload:
        uvicorn.run("paper_agent.server.app:app", host=host, port=port, reload=True)
    else:
        uvicorn.run("paper_agent.server.app:app", host=host, port=port)


@app.command()
def doctor() -> None:
    """环境自检：配置 → API Key → 平台连通 → 数据源连通 → LangGraph 图执行。"""
    console.print("[bold]paper-agent 环境自检[/bold]\n")
    table = Table(show_header=True, header_style="bold")
    table.add_column("检查项", style="cyan")
    table.add_column("状态")
    table.add_column("详情", overflow="fold")

    try:
        cfg = load_config()
        table.add_row("配置文件 config.yaml", OK, "加载成功")
    except Exception as exc:
        table.add_row("配置文件 config.yaml", FAIL, str(exc))
        console.print(table)
        raise typer.Exit(code=1) from exc

    platform_ok = _check_endpoint(table, "Embedding 平台", resolve_endpoint(cfg["embedding"]), is_embedding=True)
    platform_ok &= _check_endpoint(table, "LLM 平台", resolve_endpoint(cfg["llm"]), is_embedding=False)
    sources_ok = _check_sources(table, cfg)

    try:
        result = run_hello()
        table.add_row("LangGraph 图执行", OK, result["message"])
        graph_ok = True
    except Exception as exc:
        table.add_row("LangGraph 图执行", FAIL, str(exc))
        graph_ok = False

    console.print(table)
    core_ok = platform_ok and graph_ok
    if core_ok and sources_ok:
        console.print("\n[bold green]结论：全部检查通过。[/bold green]")
    elif core_ok:
        console.print(
            "\n[bold yellow]LLM/图执行正常，但部分数据源不可达[/bold yellow]："
            "检索会自动降级到可用源；如需 arXiv，请在 config.yaml 配置 proxy。"
        )
    else:
        console.print(
            "\n[bold yellow]图执行正常，但平台尚未连通，还差 API Key：[/bold yellow]\n"
            "1) 复制 .env.example 为 .env；\n"
            "2) 填入 Key（硅基流动 https://cloud.siliconflow.cn ｜ 智谱 https://open.bigmodel.cn）；\n"
            "3) 重新运行 [cyan]pa doctor[/cyan]。"
        )
        raise typer.Exit(code=1)


@app.command()
def prune(
    status: str = typer.Option("", "--status", help="按状态批量删除（如 discovered / download_failed）"),
    tag: str = typer.Option("", "--tag", help="按分组标签删除"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """批量删除论文（全链清理：账本 + 向量块 + 原文/解析/知识产物）。

    用于治理「检索全量入库」的历史堆积：如 `pa prune --status discovered`。
    涉及已处理论文（parsed/analyzed/indexed）时会额外警示。
    """
    if not status and not tag:
        console.print(f"{FAIL} 需要 --status 或 --tag 指定删除范围（防止误删全库）。")
        raise typer.Exit(code=1)
    cfg = load_config()
    lib = _library(cfg)
    try:
        targets = lib.list(status=status or None, tag=tag or None)
        if not targets:
            console.print("没有匹配的论文，未删除任何内容。")
            return
        processed = sum(1 for r in targets if r.status in ("parsed", "analyzed", "indexed"))
        sample = "\n".join(f"  - {r.paper.title[:56]}" for r in targets[:8])
        more = f"\n  …等共 {len(targets)} 篇" if len(targets) > 8 else ""
        console.print(
            f"[bold]将删除 {len(targets)} 篇论文及其全部产物[/bold]（原文/解析/知识/向量块）：\n"
            + sample + more
        )
        if processed:
            console.print(f"{WARN} 其中 {processed} 篇已有解析/分析产物，删除后需重新处理。")
        if not yes and not typer.confirm("确认删除？此操作不可恢复。"):
            console.print("已取消。")
            return
        ok = 0
        for rec in targets:
            try:
                if delete_paper(rec.paper.source_id, cfg=cfg, lib=lib):
                    ok += 1
            except Exception as exc:
                console.print(f"{FAIL} [dim]{rec.paper.source_id}[/dim] {str(exc)[:120]}")
        console.print(f"{OK} 已删除 {ok}/{len(targets)} 篇。")
    finally:
        lib.close()


def _check_sources(table: Table, cfg: dict) -> bool:
    """逐源连通探测；返回是否全部可用。"""
    proxy = cfg.get("proxy", "") or ""
    source_cfg = cfg.get("sources", {}) or {}
    all_ok = True
    for name, module in SOURCE_MODULES.items():
        label = f"数据源 · {name}"
        if not (source_cfg.get(name) or {}).get("enabled", True):
            table.add_row(label, SKIP, "已在 config.yaml 停用")
            continue
        try:
            if name == "openalex":
                email = (source_cfg.get("openalex") or {}).get("email", "") or ""
                detail = module.probe(proxy=proxy, email=email)
            else:
                detail = module.probe(proxy=proxy)
            table.add_row(label, OK, detail)
        except SourceUnavailable as exc:
            hint = "可在 config.yaml 配置 proxy" if name == "arxiv" else "检查网络后重试"
            table.add_row(label, FAIL, f"{exc}——{hint}")
            all_ok = False
    return all_ok


def _check_endpoint(table: Table, label: str, ep: EndpointConfig, *, is_embedding: bool) -> bool:
    if not ep.base_url or not ep.model:
        table.add_row(label, SKIP, "config.yaml 未配置 base_url/model")
        return False
    if not ep.api_key:
        table.add_row(label, SKIP, ".env 中未找到 API Key")
        return False
    client = OpenAI(base_url=ep.base_url, api_key=ep.api_key, timeout=30, max_retries=1)
    start = time.perf_counter()
    try:
        if is_embedding:
            client.embeddings.create(model=ep.model, input=["连通性测试"])
        else:
            client.chat.completions.create(
                model=ep.model,
                messages=[{"role": "user", "content": "请只回复两个字符：OK"}],
                max_tokens=8,
                temperature=0,
            )
        cost = time.perf_counter() - start
        table.add_row(label, OK, f"{ep.model} @ {ep.base_url}（耗时 {cost:.1f}s）")
        return True
    except Exception as exc:
        cost = time.perf_counter() - start
        table.add_row(label, FAIL, f"{ep.model} @ {ep.base_url}（耗时 {cost:.1f}s）：{exc}")
        return False
