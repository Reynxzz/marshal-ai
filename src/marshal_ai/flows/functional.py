"""Flow 4: Functional check on the test track.

The car runs a scripted routine; telemetry says what the car thinks it did, the
camera says what happened. Code checks telemetry, Gemini judges each clip, and an
item passes only when both agree.
"""

from __future__ import annotations

import logging

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

log = logging.getLogger(__name__)

ROUTINE = "R-12"
CAMERA_MIN_CONFIDENCE = 0.6


class StepSeen(BaseModel):
    seen: bool = Field(description="the expected result is visible in the clip")
    confidence: float = Field(ge=0, le=1)
    observation: str = Field(description="what the clip shows, one sentence")


SYSTEM = """You watch short test-track clips of finished cars. For each clip you get the expected
result. Say whether it is clearly visible, with confidence, and what you actually see. Check every
lamp named, e.g. a brake light test needs left, right and third brake lamps lit."""

# Absolute judgement of a lamp does not work. Asked whether headlights were lit in a night
# frame where they demonstrably were not, both gemini-2.5-flash and gemini-2.5-pro answered
# "both headlights are lit" at confidence 1.0 - so a dark lamp passed and the check could
# never catch the fault it exists for. Given a known-good reference frame of the same step to
# compare against, the same model answered correctly at 0.99 both ways. End-of-line rigs
# compare against a golden reference for the same reason.
SEQUENCE_SYSTEM = """You are shown several frames of one test-track step, in order, a fraction of a
second apart. Judge whether the expected result happens anywhere across them - an indicator counts as
blinking if it is lit in some frames and dark in others, and a movement counts if the car's position
changes across the frames as described. Say what you actually see; do not assume the step succeeded."""

REFERENCE_SYSTEM = """You compare two frames of the same test-track step. The FIRST is a reference
recorded from a car that passed this step. The SECOND is the car under test. Decide whether the
expected result is present in the SECOND frame, judging by how it differs from the reference.
Report what you actually see. Do not assume the car behaved correctly."""


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
        # vin -> {step: Media}: kept so a demo can write out the frames a verdict used
        self._last_clips: dict[str, dict[int, Media]] = {}
        # step name -> frame from a car that passed it, used as the comparison reference
        self.references: dict[str, Media] = {}

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
        self._last_clips[run.vin] = dict(run.clips)
        return await self._record(run.vin, list(items))

    def _sequence(self, vin: str, step: RoutineStep) -> list:
        """The step as ordered frames, when the camera can supply them."""
        supplier = getattr(self.rt.track_camera, "sequence", None)
        if supplier is None:
            return []
        try:
            return supplier(vin, step.step, step.name)
        except Exception as exc:
            log.warning("no clip for step %s (%s): %s: %s",
                        step.step, step.name, type(exc).__name__, exc)
            return []

    async def _judge(self, run: _Run, step: RoutineStep) -> ChecklistItem:
        reported = run.reported.get(step.step)
        telemetry_ok = bool(reported and TELEMETRY_CHECKS[step.name](reported.telemetry))
        clip = run.clips.get(step.step)
        if clip is None:
            clip = self.rt.track_camera.clip(run.vin, step.step, step.name)
        # The camera is the independent source. Telemetry is deliberately NOT shown to the
        # model: including it ("Telemetry reported: {'headlights': 'ON'}") led Gemini to
        # confirm a lamp that was demonstrably dark in the frame, at confidence 1.0, so the
        # check could never catch the disagreement it exists to catch.
        # A blink phase, a stopping distance and a turning circle are not states a frame can
        # hold, so a single still cannot confirm them however good the model is - the design
        # calls for a 3 s clip for exactly this reason. Evidence that describes the scene rather
        # than freezing it can still be judged, which is why this asks about the clip in hand
        # and not only about the step.
        if not step.camera_demonstrable and clip.truth is None:
            # Nothing happened for a camera to see: the car is held stationary for the step, so
            # its telemetry is scripted. Recorded as telemetry-only rather than as a step the
            # camera failed, which is what the design scopes when it says steps 1 to 4 can be
            # demonstrated in CARLA.
            log.info("functional: step %s (%s) is telemetry-only in this build",
                     step.step, step.name)
            return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                                 camera_ok=None, camera_confidence=None, clip_uri=clip.uri,
                                 camera_applicable=False)

        if not step.still_confirmable and clip.truth is None:
            frames = self._sequence(run.vin, step)
            if len(frames) < 2:
                # Still nothing to judge. camera_ok stays None - unconfirmed, which is not the
                # same as contradicted. Judged from one frame anyway, Gemini answered "no" at
                # confidence 1.0 and failed healthy cars.
                log.info("functional: step %s (%s) needs a clip and none was held",
                         step.step, step.name)
                return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                                     camera_ok=None, camera_confidence=None, clip_uri=clip.uri)
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected across these frames, which are in order: "
                      f"{step.expected_on_camera}.\n"
                      "Judge only what the frames show. Do not assume the car behaved correctly.")
            seen = await ask(self.rt, "track.step", prompt, StepSeen, frames, SEQUENCE_SYSTEM)
            camera_ok = seen.seen if seen.confidence >= CAMERA_MIN_CONFIDENCE else None
            return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                                 camera_ok=camera_ok, camera_confidence=seen.confidence,
                                 clip_uri=frames[-1].uri)

        reference = self.references.get(step.name)
        if reference is not None:
            # Both frames reach Gemini prefixed with their label, so they must be labelled
            # differently: sent as two identical "[track camera]" images the model could not
            # tell which was the reference and confirmed a dark lamp.
            first = Media(mime=reference.mime, data=reference.data, uri=reference.uri,
                          label=f"reference frame: a car that passed step {step.step}")
            second = Media(mime=clip.mime, data=clip.data, uri=clip.uri,
                           label="frame under test: the car being checked now")
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected in the frame under test: {step.expected_on_camera}.")
            seen = await ask(self.rt, "track.step", prompt, StepSeen,
                             [first, second], REFERENCE_SYSTEM)
        else:
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected on camera: {step.expected_on_camera}.\n"
                      f"Judge only what this frame shows. Do not assume the car behaved correctly.")
            seen = await ask(self.rt, "track.step", prompt, StepSeen, [clip], SYSTEM)
        camera_ok = seen.seen if seen.confidence >= CAMERA_MIN_CONFIDENCE else None
        if camera_ok and telemetry_ok and step.name not in self.references:
            # A step both sources agree on becomes the reference the next car is compared to.
            self.references[step.name] = clip
        return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                             camera_ok=camera_ok, camera_confidence=seen.confidence,
                             clip_uri=clip.uri)

    async def _record(self, vin: str, items: list[ChecklistItem]) -> InspectionResult:
        previous = self.rt.store.get(InspectionResult, f"{vin}:functional")
        if previous is not None:        # re-inspection: replace only the repeated steps
            redone = {i.step for i in items}
            items = sorted([i for i in previous.checklist if i.step not in redone] + items,
                           key=lambda i: i.step)
        # Three outcomes, because there are three cases. A camera that contradicts telemetry is a
        # failure; a step whose evidence could not confirm either way needs a person; only a car
        # where both sources agree on every step passes unattended.
        if any(i.telemetry_ok is False or i.camera_ok is False for i in items):
            verdict = Verdict.FAIL
        elif any(i.camera_ok is None and i.camera_applicable for i in items):
            verdict = Verdict.REVIEW
        else:
            verdict = Verdict.PASS
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
