"""应用配置。

使用 pydantic-settings 从 stock-service/.env 读取 NAS PostgreSQL 连接参数，
并自动拼装 SQLAlchemy 异步驱动连接串（postgresql+asyncpg://）。
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# stock-service/ 根目录（app/core/config.py -> parents[2]）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置，字段名与 .env 中的环境变量名一一对应。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    APP_NAME: str = "stock-service"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ---------- NAS PostgreSQL ----------
    POSTGRES_HOST: str = "192.168.x.x"
    # NAS Docker 外部映射端口
    POSTGRES_PORT: int = 5434
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = Field(..., description="NAS PostgreSQL 密码，必须由 .env 提供")
    POSTGRES_DB: str = "portfolio_db"

    # ---------- 连接池 ----------
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600
    # 建立连接超时（秒），避免 NAS 离线时请求长时间挂起
    DB_CONNECT_TIMEOUT: int = 5
    DB_ECHO: bool = False

    # ---------- CORS ----------
    # 逗号分隔的允许来源列表
    CORS_ORIGINS: str = "http://localhost:5173"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """异步连接串；用户名/密码做百分号编码，避免特殊字符破坏 URL 结构。"""
        user = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origins(self) -> list[str]:
        """把逗号分隔的配置解析成列表。"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免重复解析 .env。"""
    return Settings()


settings = get_settings()
