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
from .routers import (
    analysis,
    books,
    chapters,
    fanqie,
    original,
    settings,
    state,
    system,
    writing,
)

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

    # 一个网关都没有时，用环境变量 / 旧配置自动建一个，避免新用户进来一片空白
    try:
        from .database import SessionLocal
        from .settings_store import (
            create_default_provider,
            missing_model_warnings,
            placeholder_model_warnings,
            resolve_llm_config,
        )

        session = SessionLocal()
        try:
            created = create_default_provider(session)
            if created is not None:
                logger.info("已自动创建默认 LLM 网关：%s", created.name)

            config = resolve_llm_config(session)

            # 模型名还是旧版本的占位值就直接提醒，别让用户等到生成时才吃 403
            warnings = placeholder_model_warnings(config)
            if warnings:
                logger.warning(
                    "当前网关还在用旧版本的占位模型名：%s\n"
                    "  这些名字在真实账号里通常不存在，生成时会报 403 / 模型不存在。\n"
                    "  解决：打开网页「设置 → 模型路由」，点「拉取可用模型」，"
                    "再点红色提示里的「一键修正」，最后「保存设置」。",
                    "、".join(warnings),
                )

            # 一个模型名都没有：现在生成会直接报错（不再瞎猜名字），提前说清楚
            missing = missing_model_warnings(config)
            if missing and not warnings:
                logger.warning(
                    "以下档位还没有配置模型名：%s\n"
                    "  生成前请打开网页「设置 → 模型路由」，点「拉取可用模型」，"
                    "再点「一键修正」自动填好，最后「保存设置」。",
                    "、".join(missing),
                )
        finally:
            session.close()
    except Exception:  # noqa: BLE001  建网关失败不应阻止启动
        logger.warning("自动创建默认网关失败（可在设置页手动新建）", exc_info=True)

    logger.info("API Key 来源：%s", "环境变量" if env.deepseek_api_key else "网关配置 / 数据库")
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

# 注册顺序有讲究：静态前缀（/original）必须排在 /{book_id} 之前，
# 否则 FastAPI 会把 "original" 当成 book_id 去匹配。
for module in (system, books, original, analysis, state, chapters, writing, settings, fanqie):
    app.include_router(module.router)


# ----------------------------------------------------------------------
# 生产模式（Docker）下直接托管前端构建产物；开发模式前端由 Vite 提供
# ----------------------------------------------------------------------
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if not FRONTEND_DIST.exists():
    # 这时访问 http://localhost:8000/ 会 404，是最常见的困惑，直接把解法打出来
    logger.warning(
        "前端还没构建（%s 不存在），所以 http://localhost:8000/ 会返回 404。\n"
        "  解决：cd frontend && npm install && npm run build，然后重启后端；\n"
        "  或者开发时改用 Vite：cd frontend && npm run dev（打开 http://localhost:5173）。\n"
        "  （接口本身不受影响，http://localhost:8000/docs 一直可用）",
        FRONTEND_DIST,
    )

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
