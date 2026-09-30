"""MCP server 命令：pa mcp——stdio 传输，供 Claude Desktop / Cursor 等 host 接入。

工具面与护栏不在本层（复用 ToolExecutor），这里只负责启动 stdio 循环；
stdout 被 JSON-RPC 协议独占，任何提示信息只能走 stderr。
"""

from __future__ import annotations

import typer

from paper_agent.cli.core import app


@app.command("mcp")
def mcp_cmd() -> None:
    """以 MCP stdio server 运行（接入方法见 README「MCP 接入」）。"""
    from paper_agent.mcp_server import main  # 惰性导入：其余 pa 命令不背 mcp 依赖

    main()
