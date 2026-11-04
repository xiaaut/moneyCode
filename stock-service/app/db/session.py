"""异步数据库会话管理。

统一在此创建 AsyncEngine 与 Session 工厂，业务侧只需依赖 get_db。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.ASYNC_DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,          # 常驻连接数
    max_overflow=settings.DB_MAX_OVERFLOW,    # 峰值可额外创建的连接数
    pool_pre_ping=True,                       # 取连接前先探活，规避 NAS 断连后的死连接
    pool_recycle=settings.DB_POOL_RECYCLE,    # 每小时回收连接，防止被中间设备静默断开
    pool_timeout=30,
    connect_args={
        "timeout": settings.DB_CONNECT_TIMEOUT,
        "server_settings": {"application_name": settings.APP_NAME},
    },
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：每个请求一个会话，异常自动回滚，退出自动关闭。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
