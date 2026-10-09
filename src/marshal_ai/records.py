"""The records every agent shares (design doc: "Shared data, events and human authority").

The design's six, plus proposals: the decisions an agent proposes and a human makes (the design's
"Human authority" table), kept so the dashboard can show and decide them.

Live copies go to Firestore, history to BigQuery; images and clips are Cloud Storage URIs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(timezone.utc)


class CarState(str, Enum):
    LINE_END = "line_end"
    INSPECTION_GATE = "inspection_gate"
    REVIEW = "review"
    TEST_TRACK = "test_track"
    REPORT = "report"
    REWORK = "rework"
    YARD = "yard"
    SHIPPED = "shipped"


class Verdict(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class BuildSheet(BaseModel):
    model: str
    colour: str
    trim: str = "standard"
    options: list[str] = Field(default_factory=list)   # e.g. "roof_rails", "rear_badge"
    truck_id: str | None = None                        # outbound truck the car ships on


class Car(BaseModel):
    vin: str
    build_sheet: BuildSheet
    state: CarState = CarState.LINE_END
    location: str = "line_end"                         # plant map point
    mission_id: str | None = None
    yard_slot: str | None = None
    do_not_ship: bool = False
    flags: list[str] = Field(default_factory=list)     # e.g. "untested", "rework_waiting"
    released_at: datetime | None = None
    yard_at: datetime | None = None
    updated_at: datetime = Field(default_factory=now)


class AssistanceCase(BaseModel):
    case_id: str
    vin: str
    location: str
    situation: str = ""                                # one-line summary, written by Gemini
    clip_uri: str | None = None
    options: list[str]                                 # offered by the driving system, never invented
    ranking: list[dict] = Field(default_factory=list)  # [{"option", "reason", "risk"}], best first
    people_or_moving_equipment: bool = False
    recommended: str | None = None
    confidence: float | None = None
    choice: str | None = None
    resolved_by: str | None = None                     # human id; agents never resolve
    escalated: bool = False
    opened_at: datetime = Field(default_factory=now)
    resolved_at: datetime | None = None


class ZoneFinding(BaseModel):
    zone: str                                          # e.g. rear_left_door
    type: str                                          # scratch, dent, panel_gap, missing_part, ...
    size_cm: float | None = None
    confidence: float
    image: str | None = None
    observation: str | None = None                     # what the inspector saw, in its own words


class ChecklistItem(BaseModel):
    step: int
    name: str                                          # e.g. brake_lights
    telemetry_ok: bool | None = None
    camera_ok: bool | None = None
    camera_confidence: float | None = None
    # What the camera judge actually said, in its own words. Gemini has always written this - the
    # schema asks for "what the clip shows, one sentence" - and it used to be dropped on the floor,
    # leaving a bare true/false behind. A verdict nobody can read the reasoning for is a verdict
    # nobody can check, and it is the reasoning that makes a second source worth having.
    observation: str | None = None
    clip_uri: str | None = None
    # False when the simulation does not perform this step, so no camera could confirm it and the
    # step rests on telemetry alone. Kept on the record because a report that says PASS should be
    # able to say how much of it two sources agreed on.
    camera_applicable: bool = True

    @property
    def passed(self) -> bool:
        if not self.camera_applicable:
            return bool(self.telemetry_ok)
        return bool(self.telemetry_ok) and bool(self.camera_ok)


class InspectionResult(BaseModel):
    vin: str
    kind: Literal["visual", "functional"]
    verdict: Verdict
    findings: list[ZoneFinding] = Field(default_factory=list)
    build_sheet_mismatches: list[str] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    # What the inspector saw, per view, in its own words - kept for a pass as well as a failure. A
    # PASS with nothing to read is indistinguishable from a stub that always passes.
    observations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)


class ReworkTicket(BaseModel):
    bay: str                                           # plant map point, e.g. rework.electrical
    defect_type: str
    reason: str
    confirmed_by: str | None = None
    done_at: datetime | None = None


class CarReport(BaseModel):
    vin: str
    verdict: Verdict
    summary: str = ""
    findings: list[ZoneFinding] = Field(default_factory=list)
    failed_checks: list[ChecklistItem] = Field(default_factory=list)
    rework: list[ReworkTicket] = Field(default_factory=list)
    reinspection: list[InspectionResult] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now)


class ScenarioStatus(str, Enum):
    DRAFT = "draft"
    VALID = "valid"
    DRY_RUN_OK = "dry_run_ok"
    IN_LIBRARY = "in_library"
    REJECTED = "rejected"


class Scenario(BaseModel):
    id: str
    spec: dict                                         # marshal_ai.lab.spec.ScenarioSpec as JSON
    source: str
    status: ScenarioStatus = ScenarioStatus.DRAFT
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)       # e.g. "regression", "from-incident"
    notes: str = ""                                    # validator errors, failure report
    created_at: datetime = Field(default_factory=now)


class TestRun(BaseModel):
    __test__ = False                                   # not a pytest class

    run_id: str
    scenario_id: str
    variant: str                                       # e.g. SC-0142-v17
    params: dict = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    passed: bool
    failed_criteria: list[str] = Field(default_factory=list)
    clip_uri: str | None = None
    created_at: datetime = Field(default_factory=now)


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Proposal(BaseModel):
    """Something an agent proposes and a human must decide: a re-plan, a rework, a visual review."""

    proposal_id: str                                   # P-001, unique across runs
    kind: Literal["replan", "rework", "review"]
    summary: str
    payload: dict = Field(default_factory=dict)        # what deciding acts on, e.g. the plan or VIN
    status: ProposalStatus = ProposalStatus.PENDING
    decided_by: str | None = None
    created_at: datetime = Field(default_factory=now)
    decided_at: datetime | None = None

