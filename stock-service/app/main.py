"""stock-service 服务入口。

- lifespan：启动时探活主库（Mac 本地 PostgreSQL，失败不阻断启动，由健康检查持续反映真实状态），
  关闭时释放连接池。
- CORS：放行 Vite 开发服务器 http://localhost:5173。
- GET /api/v1/health：真实执行 SELECT 1，返回数据库连通性与耗时。
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine, get_db

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("stock_service")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("启动 %s v%s", settings.APP_NAME, settings.APP_VERSION)
    # 启动探活：主库（Mac 本地）异常只告警，不阻断服务启动
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info(
            "主库数据库连接成功 -> %s:%s/%s",
            settings.POSTGRES_HOST,
            settings.POSTGRES_PORT,
            settings.POSTGRES_DB,
        )
    except Exception as exc:  # noqa: BLE001 - 启动阶段需要吞掉所有连接异常
        logger.warning(
            "主库数据库首次连接失败（服务仍启动，请检查 .env 中的 DATABASE_URL）: %s: %s",
            type(exc).__name__,
            exc,
        )

    yield

    logger.info("关闭 %s，释放数据库连接池", settings.APP_NAME)
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="个人投资组合后端服务（FastAPI + SQLAlchemy Async + NAS PostgreSQL）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务路由：正式入口 /api/v1
app.include_router(api_router, prefix="/api/v1")
# 兼容入口 /api：前端历史路径就是 /api/portfolio，避免在 Vite 代理里做 rewrite
app.include_router(api_router, prefix="/api", include_in_schema=False)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """前端统一读 json.message，这里把 FastAPI 默认的 detail 转成 message。"""
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(item) for item in first.get("loc", [])[1:])
    detail = first.get("msg", "参数校验失败")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"message": f"参数校验失败：{location} {detail}".strip(), "errors": exc.errors()},
    )


@app.get("/", tags=["system"], summary="服务基本信息")
async def root() -> dict[str, str]:
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get(
    "/api/v1/health",
    tags=["system"],
    summary="健康检查（含 NAS 数据库真实连通状态）",
    responses={503: {"description": "数据库不可用，服务处于降级状态"}},
)
async def health_check(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """执行 SELECT 1 校验数据库；不可用时返回 503 并带上失败原因。"""
    started = time.perf_counter()
    db_status = "connected"
    error: str | None = None

    try:
        result = await db.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            raise RuntimeError("SELECT 1 返回了非预期结果")
    except Exception as exc:  # noqa: BLE001 - 健康检查需要把任何异常转成状态输出
        db_status = "disconnected"
        error = f"{type(exc).__name__}: {exc}"
        logger.error("健康检查失败，数据库不可用: %s", error)

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    healthy = db_status == "connected"

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if healthy else "degraded",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {
            "status": db_status,
            "host": settings.POSTGRES_HOST,
            "port": settings.POSTGRES_PORT,
            "database": settings.POSTGRES_DB,
            "latency_ms": latency_ms,
            "error": error,
        },
    }
