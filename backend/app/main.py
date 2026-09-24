"""FastAPI 应用入口。

启动方式：
    uvicorn app.main:app --reload --port 8000   （在 backend/ 目录下）
文档地址：
    http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import env
from .database import init_db
from .routers import analysis, books, chapters, fanqie, settings, system, writing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 同时写一份日志文件，出问题时可以直接把文件发出来定位
try:
    _log_dir = env.data_path / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(_log_dir / "app.log", encoding="utf-8")
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(_file_handler)
except Exception:  # noqa: BLE001
    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logger.info("数据库就绪：%s", env.data_path / "app.db")
    logger.info("API Key 来源：%s", "环境变量" if env.deepseek_api_key else "设置页/数据库")
    yield


app = FastAPI(
    title="本地 AI 小说续写",
    description="导入番茄小说 / TXT / EPUB，AI 拆书 + RAG 续写，数据全部保存在本地。",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=env.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (system, books, chapters, analysis, writing, settings, fanqie):
    app.include_router(module.router)


# ----------------------------------------------------------------------
# 生产模式（Docker）下直接托管前端构建产物；开发模式前端由 Vite 提供
# ----------------------------------------------------------------------
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str) -> FileResponse:
        if path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not Found")
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
