from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 数据库（本地开发用 SQLite，生产用 MySQL）
    DATABASE_URL: str = "sqlite:///./license.db"

    # JWT 配置
    JWT_SECRET: str = "your-secret-key-change-in-production-min-32-chars"
    JWT_ALGORITHM: str = "HS256"
    TOKEN_EXPIRE_HOURS: int = 24

    # 授权码签名密钥
    LICENSE_SIGNING_KEY: str = "your-secret-signing-key-min-32-chars"
    CACHE_SIGNING_SECRET: str = "another-secret-key-for-cache"

    # 外部 API
    DEEPSEEK_API_KEY: str = ""
    IFLYTEK_APP_ID: str = ""
    IFLYTEK_API_KEY: str = ""
    IFLYTEK_API_SECRET: str = ""

    # 离线宽限期
    OFFLINE_GRACE_DAYS: int = 7

    # 心跳间隔
    HEARTBEAT_INTERVAL_SEC: int = 1800


settings = Settings()
