from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single access point for configuration; service code never calls os.getenv."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- App ----------
    app_name: str = "Task Management API"
    debug: bool = False
    backend_port: int = 8002
    cors_origins: str = "*"

    # ---------- DB ----------
    database_url: str
    test_database_url: str | None = None

    # ---------- Redis ----------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # ---------- JWT ----------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ---------- Business rules ----------
    max_active_tasks_per_user: int = 10
    comment_rate_limit: int = 5
    comment_rate_period_seconds: int = 60
    slow_request_threshold_ms: int = 500

    # ---------- External quotes API ----------
    quotes_api_url: str = "https://dummyjson.com/quotes/random"
    quotes_api_timeout_seconds: float = 5.0

    # ---------- Cache ----------
    analytics_cache_ttl_seconds: int = 60

    system_user_username: str = Field(default="System")
    system_user_email: str = Field(default="system@internal.local")

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
