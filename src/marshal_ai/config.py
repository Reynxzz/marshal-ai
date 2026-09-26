"""Runtime settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: os.getenv("MARSHAL_MODEL", "gemini-2.5-flash"))
    bus: str = field(default_factory=lambda: os.getenv("MARSHAL_BUS", "memory"))
    store: str = field(default_factory=lambda: os.getenv("MARSHAL_STORE", "memory"))
    driving: str = field(default_factory=lambda: os.getenv("MARSHAL_DRIVING", "fake"))
    llm: str = field(default_factory=lambda: os.getenv("MARSHAL_LLM", "gemini"))   # or "offline"
    # "none" keeps gs:// URIs as labels; "gcs" uploads the photo behind each verdict
    artifacts: str = field(default_factory=lambda: os.getenv("MARSHAL_ARTIFACTS", "none"))
    bucket: str | None = field(default_factory=lambda: os.getenv("MARSHAL_BUCKET"))
    # "none" keeps only the live copy; "bq" appends every state change to BigQuery
    history: str = field(default_factory=lambda: os.getenv("MARSHAL_HISTORY", "none"))
    gcp_project: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT"))
    carla_host: str = field(default_factory=lambda: os.getenv("CARLA_HOST", "localhost"))
    carla_port: int = field(default_factory=lambda: int(os.getenv("CARLA_PORT", "2000")))
    carla_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("CARLA_TIMEOUT_S", "20"))
    )
    carla_version: str = field(default_factory=lambda: os.getenv("CARLA_VERSION", "0.9.16"))


settings = Settings()
