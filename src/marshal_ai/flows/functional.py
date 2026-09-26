"""Flow 4: Functional check on the test track.

The car runs a scripted routine; telemetry says what the car thinks it did, the
camera says what happened. Code checks telemetry, Gemini judges each clip, and an
item passes only when both agree.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from marshal_ai.driving import MissionDone, MissionGo, TrackStep
from marshal_ai.driving.routines import TELEMETRY_CHECKS, RoutineStep, routine_steps
from marshal_ai.events import Topic
from marshal_ai.flows.base import ask, update_car
from marshal_ai.llm import Media
from marshal_ai.records import CarState, ChecklistItem, InspectionResult, Verdict

if TYPE_CHECKING:
    from marshal_ai.app import MarshalApp

ROUTINE = "R-12"
CAMERA_MIN_CONFIDENCE = 0.6


class StepSeen(BaseModel):
    seen: bool = Field(description="the expected result is visible in the clip")
    confidence: float = Field(ge=0, le=1)
    observation: str = Field(description="what the clip shows, one sentence")


SYSTEM = """You watch short test-track clips of finished cars. For each clip you get the expected
result. Say whether it is clearly visible, with confidence, and what you actually see. Check every
lamp named, e.g. a brake light test needs left, right and third brake lamps lit."""


@dataclass
class _Run:
    vin: str
    steps: list[RoutineStep]
    reported: dict[int, TrackStep] = field(default_factory=dict)
    clips: dict[int, Media] = field(default_factory=dict)


class FunctionalChecker:
    def __init__(self, app: "MarshalApp"):
        self.app = app
        self.rt = app.rt
        self.runs: dict[str, _Run] = {}

    async def start(self, vin: str, steps: list[int] | None = None) -> str:
        """Send the car through the routine and on to the report point."""
        mission = MissionGo(mission_id=self.rt.next_id("M", 4), vin=vin,
                            waypoints=["test_track", "report_point"], routine=ROUTINE, routine_steps=steps)
        self.runs[mission.mission_id] = _Run(vin, routine_steps(ROUTINE, steps))
        await self.rt.missions.send(mission)
        update_car(self.rt, vin, state=CarState.TEST_TRACK, mission_id=mission.mission_id)
        return mission.mission_id

    def on_step(self, event: TrackStep) -> None:
        run = self.runs.get(event.mission_id)
        if run is not None:
            run.reported[event.step] = event
            run.clips[event.step] = self.rt.track_camera.clip(
                run.vin, event.step, event.name
            )

    async def on_mission_done(self, event: MissionDone) -> InspectionResult | None:
        run = self.runs.pop(event.mission_id, None)
        if run is None:
            return None
        items = await asyncio.gather(*(self._judge(run, s) for s in run.steps))
        return await self._record(run.vin, list(items))

    async def _judge(self, run: _Run, step: RoutineStep) -> ChecklistItem:
        reported = run.reported.get(step.step)
        telemetry_ok = bool(reported and TELEMETRY_CHECKS[step.name](reported.telemetry))
        clip = run.clips.get(step.step)
        if clip is None:
            clip = self.rt.track_camera.clip(run.vin, step.step, step.name)
        prompt = (f"Step {step.step}: {step.command}.\nExpected on camera: {step.expected_on_camera}.\n"
                  f"Telemetry reported: {reported.telemetry if reported else 'nothing'}")
        seen = await ask(self.rt, "track.step", prompt, StepSeen, [clip], SYSTEM)
        camera_ok = seen.seen if seen.confidence >= CAMERA_MIN_CONFIDENCE else None
        return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                             camera_ok=camera_ok, camera_confidence=seen.confidence,
                             clip_uri=clip.uri)

    async def _record(self, vin: str, items: list[ChecklistItem]) -> InspectionResult:
        previous = self.rt.store.get(InspectionResult, f"{vin}:functional")
        if previous is not None:        # re-inspection: replace only the repeated steps
            redone = {i.step for i in items}
            items = sorted([i for i in previous.checklist if i.step not in redone] + items,
                           key=lambda i: i.step)
        verdict = Verdict.PASS if all(i.passed for i in items) else Verdict.FAIL
        result = InspectionResult(vin=vin, kind="functional", verdict=verdict, checklist=items,
                                  created_at=self.rt.clock())
        self.rt.store.put(result)
        update_car(self.rt, vin, state=CarState.REPORT, location="report_point", mission_id=None)
        await self.rt.bus.publish(Topic.INSPECTION_RESULTS, result)
        return result

    def kpis(self) -> dict:
        results = [r for r in self.rt.store.list(InspectionResult) if r.kind == "functional"]
        failures: dict[str, int] = {}
        disagreements = 0
        for r in results:
            for i in r.checklist:
                if not i.passed:
                    failures[i.name] = failures.get(i.name, 0) + 1
                if i.telemetry_ok is not None and i.camera_ok is not None and i.telemetry_ok != i.camera_ok:
                    disagreements += 1
        return {"checked": len(results), "failures_per_item": failures,
                "telemetry_camera_disagreements": disagreements}
