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
    # "none" keeps gs:// URIs as labels; "gcs" uploads the photo behind each verdict
    artifacts: str = field(default_factory=lambda: os.getenv("MARSHAL_ARTIFACTS", "none"))
    bucket: str | None = field(default_factory=lambda: os.getenv("MARSHAL_BUCKET"))
    # "none" keeps only the live copy; "bq" appends every state change to BigQuery
    history: str = field(default_factory=lambda: os.getenv("MARSHAL_HISTORY", "none"))
    gcp_project: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT"))
    lab: str = field(default_factory=lambda: os.getenv("MARSHAL_LAB", ""))   # sim | carla
    carla_host: str = field(default_factory=lambda: os.getenv("CARLA_HOST", "localhost"))
    carla_port: int = field(default_factory=lambda: int(os.getenv("CARLA_PORT", "2000")))
    carla_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("CARLA_TIMEOUT_S", "20"))
    )
    carla_version: str = field(default_factory=lambda: os.getenv("CARLA_VERSION", "0.9.16"))
    # Judgements asked of Gemini as a decision model: one option number back, its probability from the
    # token's log-probability (llm.Decision). Comma-separated task names or prefixes; "0" for none,
    # "all" for every task that has a decision form. On by default for the lamp check only, where it
    # measured 1.0 s against 3.2 s with no wrong verdict (v8-track). The gate's screens (visual.screen,
    # visual.match) are opt-in: in Singapore they matched the full answers' accuracy at the same median
    # and a worse tail (v6-gate 4.88 s against 4.84 s), since the gate waits on reading the images.
    decisions: str = field(default_factory=lambda: os.getenv("MARSHAL_DECISIONS", "track.decide"))


settings = Settings()
