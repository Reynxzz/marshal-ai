"""All Marshal SQLModel tables.

Importing this package registers every table on ``SQLModel.metadata``; Alembic's
``env.py`` and ``create_all`` rely on that side effect.
"""

from __future__ import annotations

from marshal_api.models.assistance import (
    AssistanceCase,
    AssistanceOption,
    AssistanceStatus,
    Proposal,
    RiskLevel,
)
from marshal_api.models.base import TenantScopedModel, generate_ulid, utc_now
from marshal_api.models.iam import Plant, Tenant, User
from marshal_api.models.lab import (
    Release,
    ReleaseEvalTest,
    Scenario,
    ScenarioStatus,
    TestRun,
)
from marshal_api.models.ops import AsyncJob, EventLog, MediaArtifact
from marshal_api.models.quality import (
    BuildsheetMismatch,
    CarReport,
    FunctionalStepCheck,
    InspectionResult,
    ReworkTicket,
    Verdict,
    VisualFinding,
)
from marshal_api.models.yard import (
    BuildSheet,
    Car,
    CarState,
    Mission,
    PlantMapPoint,
    ReworkBay,
    Truck,
    Zone,
)

__all__ = [
    # base
    "TenantScopedModel",
    "generate_ulid",
    "utc_now",
    # iam
    "Tenant",
    "Plant",
    "User",
    # yard
    "CarState",
    "Zone",
    "ReworkBay",
    "PlantMapPoint",
    "Truck",
    "BuildSheet",
    "Car",
    "Mission",
    # assistance
    "AssistanceStatus",
    "RiskLevel",
    "AssistanceCase",
    "AssistanceOption",
    "Proposal",
    # quality
    "Verdict",
    "InspectionResult",
    "VisualFinding",
    "BuildsheetMismatch",
    "FunctionalStepCheck",
    "CarReport",
    "ReworkTicket",
    # lab
    "ScenarioStatus",
    "Scenario",
    "TestRun",
    "Release",
    "ReleaseEvalTest",
    # ops
    "AsyncJob",
    "MediaArtifact",
    "EventLog",
]
