"""Runtime settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Local settings and credentials (git-ignored); real environment variables win.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

# Verify TLS against the operating system's trust store instead of Python's bundled one, so
# networks that inspect HTTPS with a company root certificate (trusted by the OS) work too.
# MARSHAL_SYSTEM_CERTS=0 turns this off.
if os.getenv("MARSHAL_SYSTEM_CERTS", "1") != "0":
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: os.getenv("MARSHAL_MODEL", "gemini-2.5-flash"))
    bus: str = field(default_factory=lambda: os.getenv("MARSHAL_BUS", "memory"))
    store: str = field(default_factory=lambda: os.getenv("MARSHAL_STORE", "memory"))
    driving: str = field(default_factory=lambda: os.getenv("MARSHAL_DRIVING", "fake"))
    llm: str = field(default_factory=lambda: os.getenv("MARSHAL_LLM", "gemini"))   # or "offline"
    gcp_project: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT"))
    lab: str = field(default_factory=lambda: os.getenv("MARSHAL_LAB", ""))   # sim | scenic | carla
    render: bool = field(default_factory=lambda: os.getenv("MARSHAL_RENDER", "") in ("1", "true"))
    view: str = field(default_factory=lambda: os.getenv("MARSHAL_VIEW", ""))       # topdown | 3d
    carla_host: str = field(default_factory=lambda: os.getenv("CARLA_HOST", "localhost"))
    carla_port: int = field(default_factory=lambda: int(os.getenv("CARLA_PORT", "2000")))


settings = Settings()
