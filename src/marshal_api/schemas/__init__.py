"""Pydantic v2 request/response DTOs.

These mirror ``marshal-fe/src/lib/schemas.ts`` exactly (field names, enums and
optionality); the shared primitives live in :mod:`marshal_api.schemas.common`.
"""

from __future__ import annotations

from marshal_api.schemas.assistance import (
    AssistanceCase,
    AssistanceKpis,
    AssistanceOption,
    DecideAssistanceRequest,
)
from marshal_api.schemas.common import (
    Actor,
    CarState,
    ErrorResponse,
    FlowEvent,
    Health,
    JobStatus,
    Kpi,
    KpiResponse,
    Page,
    Pagination,
    Readiness,
    ReviewStatus,
    Role,
    Severity,
    Verdict,
)
from marshal_api.schemas.jobs import Job, JobAccepted, Media
from marshal_api.schemas.lab import (
    CreateScenarioRequest,
    Release,
    ReleaseEvalTest,
    ReviewScenarioRequest,
    Scenario,
    ScenarioDetail,
    TestRequest,
    ValidatorNote,
)
from marshal_api.schemas.overview import Overview
from marshal_api.schemas.proposals import DecideProposalRequest, Proposal
from marshal_api.schemas.quality import (
    BuildSheetMismatch,
    FunctionalInspection,
    FunctionalStep,
    InspectionKpis,
    InspectionResult,
    Report,
    ReworkTicket,
    VisualFinding,
    VisualInspection,
)
from marshal_api.schemas.yard import (
    BuildSheet,
    CarDetail,
    CarSummary,
    MapPoint,
    ReworkBay,
    ReworkBayList,
    Truck,
    Yard,
    YardMap,
    Zone,
)

__all__ = [
    # common
    "Actor",
    "CarState",
    "ErrorResponse",
    "FlowEvent",
    "Health",
    "JobStatus",
    "Kpi",
    "KpiResponse",
    "Page",
    "Pagination",
    "Readiness",
    "ReviewStatus",
    "Role",
    "Severity",
    "Verdict",
    # yard
    "BuildSheet",
    "CarDetail",
    "CarSummary",
    "MapPoint",
    "ReworkBay",
    "ReworkBayList",
    "Truck",
    "Yard",
    "YardMap",
    "Zone",
    # proposals
    "DecideProposalRequest",
    "Proposal",
    # assistance
    "AssistanceCase",
    "AssistanceKpis",
    "AssistanceOption",
    "DecideAssistanceRequest",
    # quality
    "BuildSheetMismatch",
    "FunctionalInspection",
    "FunctionalStep",
    "InspectionKpis",
    "InspectionResult",
    "Report",
    "ReworkTicket",
    "VisualFinding",
    "VisualInspection",
    # lab
    "CreateScenarioRequest",
    "Release",
    "ReleaseEvalTest",
    "ReviewScenarioRequest",
    "Scenario",
    "ScenarioDetail",
    "TestRequest",
    "ValidatorNote",
    # overview
    "Overview",
    # jobs
    "Job",
    "JobAccepted",
    "Media",
]
