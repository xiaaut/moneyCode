"""应用配置。

主备双库架构：
- 主库（Primary）：Mac 本地 PostgreSQL，DATABASE_URL 指向 127.0.0.1:5432，
  日常运行与开发全部走它，飞牛 NAS 关机时系统 100% 可用。
- 冷备（Backup）：飞牛 NAS PostgreSQL，NAS_DATABASE_URL 仅在 db_sync.py
  同步备份时使用，不参与日常请求。

兼容说明：POSTGRES_* 保留为兜底/展示字段。一旦配置了 DATABASE_URL，
启动时会自动从连接串解析并覆盖它们，因此 /health 等展示处永远反映真实主库。
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# stock-service/ 根目录（app/core/config.py -> parents[2]）
BASE_DIR = Path(__file__).resolve().parents[2]


def to_async_url(url: str) -> str:
    """把普通 PostgreSQL DSN 归一化为 SQLAlchemy 异步驱动连接串。"""
    url = url.strip()
    if not url:
        return url
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    raise ValueError(f"无法识别的数据库连接串（需以 postgresql:// 开头）: {url.split('@')[-1]}")


def to_raw_dsn(url: str) -> str:
    """去掉 +asyncpg 驱动后缀，得到 asyncpg.connect 可直接使用的 DSN。"""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


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

    # ---------- 双库连接（主库 / 冷备）----------
    # 主库：Mac 本地 PostgreSQL，日常运行默认走这里
    DATABASE_URL: str = ""
    # 冷备：飞牛 NAS PostgreSQL，按需开机，仅同步备份时连接
    NAS_DATABASE_URL: str = ""

    # ---------- 兼容字段（DATABASE_URL 留空时兜底；配置后自动被解析覆盖）----------
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "portfolio_db"

    # ---------- 连接池 ----------
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600
    # 建连超时（秒）：本地库正常 <50ms，5s 足够暴露异常
    DB_CONNECT_TIMEOUT: int = 5
    # NAS 探活超时（秒）：NAS 常关机，必须快速失败，避免拖慢日常请求
    NAS_DB_CONNECT_TIMEOUT: int = 3
    DB_ECHO: bool = False

    # ---------- CORS ----------
    # 逗号分隔的允许来源列表
    CORS_ORIGINS: str = "http://localhost:5173"

    # ---------- 数据源（同花顺主通道 + 免费备用通道）----------
    THS_API_KEY: str = ""
    THS_BASE_URL: str = "https://fuyao.aicubes.cn"
    # ths_first（默认）：同花顺优先，认证/权限/限流/上游异常时自动降级免费通道
    # free_only：强制走东财/腾讯/AKShare 免费通道
    DATA_SOURCE_MODE: str = "ths_first"

    def model_post_init(self, __context: object) -> None:
        """配置了 DATABASE_URL 时，用连接串回填 POSTGRES_*，供健康检查/日志展示。"""
        if not self.DATABASE_URL.strip():
            return
        parsed = make_url(to_async_url(self.DATABASE_URL))
        self.POSTGRES_HOST = parsed.host or "127.0.0.1"
        self.POSTGRES_PORT = parsed.port or 5432
        self.POSTGRES_USER = parsed.username or "postgres"
        self.POSTGRES_PASSWORD = parsed.password or ""
        self.POSTGRES_DB = parsed.database or "portfolio_db"

    # ---------- 派生连接串 ----------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """主库异步连接串。"""
        if self.DATABASE_URL.strip():
            return to_async_url(self.DATABASE_URL)
        if not self.POSTGRES_PASSWORD:
            raise ValueError(
                "数据库未配置：请在 stock-service/.env 中设置 DATABASE_URL"
                "（或同时提供 POSTGRES_HOST/PORT/USER/PASSWORD/DB）"
            )
        user = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def NAS_ASYNC_DATABASE_URL(self) -> str:
        """NAS 冷备异步连接串；未配置时返回空串（日常运行不需要它）。"""
        if not self.NAS_DATABASE_URL.strip():
            return ""
        return to_async_url(self.NAS_DATABASE_URL)

    @property
    def DATABASE_DSN(self) -> str:
        """主库裸 DSN，供 asyncpg 直连。"""
        return to_raw_dsn(self.ASYNC_DATABASE_URL)

    @property
    def NAS_DATABASE_DSN(self) -> str:
        """NAS 裸 DSN，供 asyncpg 直连；未配置返回空串。"""
        if not self.NAS_ASYNC_DATABASE_URL:
            return ""
        return to_raw_dsn(self.NAS_ASYNC_DATABASE_URL)

    @property
    def DB_LABEL(self) -> str:
        """主库展示标签。"""
        return f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def cors_origins(self) -> list[str]:
        """把逗号分隔的配置解析成列表。"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免重复解析 .env。"""
    return Settings()


settings = get_settings()
