"""
fxi.api.server - FastAPI 本地服务启动器
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from fxi.api.router_v1 import router as v1_router
from fxi.core.config import FxiConfig, load_config


def create_app(config: FxiConfig = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例"""
    cfg = config or load_config()
    app = FastAPI(
        title="Fxi Knowledge Base & World State Service",
        description="小说创作世界观知识库、因果图与动态状态数据中心",
        version="0.1.0"
    )
    app.state.config = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "fxi"}

    app.include_router(v1_router)
    return app


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """启动本地服务"""
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
