"""FastAPI 应用工厂：挂载 /api 路由与前端静态产物。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from paper_agent import __version__
from paper_agent.config import PROJECT_ROOT


def create_app() -> FastAPI:
    app = FastAPI(
        title="个人文献库智能体 API",
        version=__version__,
        description="个人文献库智能体：多源检索、文献入库、可溯源问答与跨论文对比",
    )

    from paper_agent.server import routes

    app.include_router(routes.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    # 前端产物（web/dist）存在时同端口托管；开发时前端走 Vite 自己的端口
    dist = PROJECT_ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
