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


def parse_task_models(text: str) -> dict[str, str]:
    """"visual.=gemini-2.5-flash,track.step=gemini-2.5-pro" -> {"visual.": ..., "track.step": ...}"""
    pairs = (item.split("=", 1) for item in text.split(",") if "=" in item)
    return {k.strip(): v.strip() for k, v in pairs if k.strip() and v.strip()}


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: os.getenv("MARSHAL_MODEL", "gemini-3.8-flash"))
    # The model for decisions (llm.Decision), if not MARSHAL_MODEL. Gemini 3 returns no
    # log-probabilities, so on it a decision's probability is 1 for its answer; a 2.5 model keeps
    # honest probabilities for the lamp check, the gate's screens, routes and parking.
    decision_model: str | None = field(default_factory=lambda: os.getenv(
        "MARSHAL_DECISION_MODEL", "gemini-2.5-flash") or None)
    # Tasks (or prefixes ending in ".") that go to another model than MARSHAL_MODEL, as
    # "visual.=gemini-2.5-flash,track.=gemini-2.5-flash"; "" for none. The gate, the damage check
    # and the lamp check stay on 2.5: more accurate and about twice as fast there (llm.py).
    task_models: dict = field(default_factory=lambda: parse_task_models(os.getenv(
        "MARSHAL_TASK_MODELS", "visual.=gemini-2.5-flash,track.=gemini-2.5-flash")))
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
    # "all" for every task that has a decision form. On by default for:
    #   track.decide   the lamp check: 0.99 s against 3.2 s, no wrong verdict (v8-track)
    #   visual.screen  the gate, per view: "any defect visible?"; a confident no clears the view
    #   visual.match   the gate: "matches the build sheet?"; a confident yes needs no mismatch list
    # The gate's screens kept its accuracy (v6-gate 0.977, no false alarm; v2-damage 93% found) at
    # about the same median as full answers (4.88 s against 4.84 s in Singapore), with a slower tail
    # and a damaged car paying for both calls; they are on for the decision's honest probabilities,
    # not for speed (Rey, 2026-10-14).
    decisions: str = field(default_factory=lambda: os.getenv(
        "MARSHAL_DECISIONS", "track.decide,visual.screen,visual.match"))
    # Who parks a car in its yard slot or bay on CARLA: "standin" (steered by hand, then set on the
    # spot) or "gemini" (Gemini chooses each manoeuvre as a decision, behind a safety check;
    # driving/parking.py, eval: python -m marshal_ai.eval.parking).
    parking: str = field(default_factory=lambda: os.getenv("MARSHAL_PARKING", "standin"))
    # With parking=gemini: "paths" (Gemini chooses among planned paths, segment by segment) or
    # "moves" (Gemini chooses every 1 m manoeuvre; did not park a car on the eval). See parking.py.
    park_mode: str = field(default_factory=lambda: os.getenv("MARSHAL_PARK_MODE", "paths"))
    # Which way a car drives to its slot (flows/route.py): "reason" asks Gemini when something is
    # reported on one of the ways (a hazard, a stopped car, other cars) and takes the shortest
    # otherwise; "always" asks whenever there is more than one way; "never" keeps the shortest.
    route: str = field(default_factory=lambda: os.getenv("MARSHAL_ROUTE", "reason"))


settings = Settings()
