"""Control Tower API settings, read from the environment (``MARSHAL_`` prefix)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the FastAPI service.

    Environment variables use the ``MARSHAL_`` prefix, so ``db_url`` is set by
    ``MARSHAL_DB_URL`` — the same variable the Alembic migrations and the data
    layer read, keeping one source of truth across the whole backend.
    """

    model_config = SettingsConfigDict(
        env_prefix="MARSHAL_", env_file=".env", extra="ignore"
    )

    app_name: str = "Marshal Control Tower API"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    debug: bool = False

    db_url: str = "sqlite+aiosqlite:///./marshal.db"

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # Demo defaults so the scaffolding answers before auth/tenant data exists.
    demo_tenant_id: str = "tenant-demo"
    demo_actor_id: str = "actor-demo"
    demo_actor_name: str = "Demo Supervisor"
    default_role: str = "supervisor"

    idempotency_ttl_seconds: int = 24 * 60 * 60

    # Gemini/Vertex model used by the clip-ingest service (MARSHAL_MODEL).
    model: str = "gemini-2.5-flash"

    # Phase 6/7 seams, wired but unused by the scaffolding.
    event_bus: str = "memory"
    carla_host: str = "localhost"
    carla_port: int = 2000
    camera_stream_url: str = "http://localhost:8080/camera.mjpg"
    media_signed_url_ttl_seconds: int = 3600
    media_bucket: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
