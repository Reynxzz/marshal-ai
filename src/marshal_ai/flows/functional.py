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
from marshal_ai.flows.base import ask, ask_or, update_car, use_decisions
from marshal_ai.llm import Media, decision_schema
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


SEEN = decision_schema("yes", "no")

# no answer: confidence 0 leaves the step unconfirmed, so the car goes to a supervisor (REVIEW)
NOT_JUDGED = StepSeen(seen=False, confidence=0.0, observation="not judged: model unavailable")


SYSTEM = """You watch short test-track clips of finished cars. For each clip you get the expected
result. Say whether it is clearly visible, with confidence, and what you actually see. Check every
lamp named, e.g. a brake light test needs left, right and third brake lamps lit."""

# Absolute judgement of a lamp does not work. Asked whether headlights were lit in a night
# frame where they demonstrably were not, both gemini-2.5-flash and gemini-2.5-pro answered
# "both headlights are lit" at confidence 1.0 - so a dark lamp passed and the check could
# never catch the fault it exists for. Given a known-good reference frame of the same step to
# compare against, the same model answered correctly at 0.99 both ways. End-of-line rigs
# compare against a golden reference for the same reason.
# Two whole frames were not enough on the plant world, in daylight: the lamps are a small part of a
# 1280x720 frame, and Gemini failed every healthy car compared with another ("the headlights are off
# in the frame under test, just as they are in the reference frame"), whatever the prompt said
# (dataset v8-track: 0.44 accuracy, every healthy car rejected). High media resolution would help, but
# Vertex allows it for a single image only. So the two are cropped to the front of the car and
# stacked into one labelled image (pair_image), sent at high resolution.
SEQUENCE_SYSTEM = """You are shown several frames of one test-track step, in order, a fraction of a
second apart. Judge whether the expected result happens anywhere across them - an indicator counts as
blinking if it is lit in some frames and dark in others, and a movement counts if the car's position
changes across the frames as described. Say what you actually see; do not assume the step succeeded."""

REFERENCE_SYSTEM = """You compare two frames of the same test-track step. The FIRST is a reference
recorded from a car that passed this step. The SECOND is the car under test. Decide whether the
expected result is present in the SECOND frame, judging by how it differs from the reference.
Report what you actually see. Do not assume the car behaved correctly."""

PAIR_SYSTEM = """You compare two crops of the same test-track step, one above the other in a single
image. The TOP crop, labelled REFERENCE, is from a car that passed this step: the expected result is
present in it. The BOTTOM crop, labelled UNDER TEST, is the car being checked. Look only at the lamps
the step names, in both, side by side; other lights (fog, bumper or grille lights) and differences in
framing, distance or angle do not count. If the lamps named show the same light as in the reference -
the same glowing strips or bulbs, about as bright - the result is present. If a lamp named glows in the
reference but is dark, grey or clearly dimmer under test, it is not. Report what you see in each crop.
Do not assume the car behaved correctly."""


# The front of the car in a track camera frame, as fractions of its width and height: the camera
# sits at the same place relative to every car, straight ahead of it (driving/carla_backend.py).
FRONT_CROP = (0.22, 0.50, 0.78, 0.88)


def pair_image(reference: Media, clip: Media) -> Media | None:
    """The reference and the frame under test, cropped to the lamps and stacked into one image.

    None when either has no bytes (a gs:// URI only) or Pillow is not installed: the caller then
    sends the two frames as they are.
    """
    if reference.data is None or clip.data is None:
        return None
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    crops = []
    for media in (reference, clip):
        try:
            im = Image.open(io.BytesIO(media.data)).convert("RGB")
        except (OSError, ValueError):       # not an image Pillow can read: send the frames as they are
            return None
        w, h = im.size
        box = (int(w * FRONT_CROP[0]), int(h * FRONT_CROP[1]), int(w * FRONT_CROP[2]), int(h * FRONT_CROP[3]))
        crops.append(im.crop(box))
    cw, ch = crops[0].size
    bar = max(28, ch // 9)
    out = Image.new("RGB", (cw, 2 * (ch + bar)), (20, 20, 20))
    draw = ImageDraw.Draw(out)
    font = ImageFont.load_default(size=bar * 2 // 3) if hasattr(ImageFont, "load_default") else None
    for i, (crop, label) in enumerate(zip(crops, ("REFERENCE (passed this step)", "UNDER TEST"))):
        top = i * (ch + bar)
        draw.text((10, top + bar // 6), label, fill=(255, 255, 255), font=font)
        out.paste(crop.resize((cw, ch)), (0, top + bar))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92)
    return Media(mime="image/jpeg", data=buf.getvalue(), uri=clip.uri,
                 label="reference above, car under test below", truth=clip.truth)


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
                                 observation="the simulation does not perform this step, so no "
                                             "camera can confirm it",
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
                                     camera_ok=None, camera_confidence=None, clip_uri=clip.uri,
                                     observation="no clip was held for this step")
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected across these frames, which are in order: "
                      f"{step.expected_on_camera}.\n"
                      "Judge only what the frames show. Do not assume the car behaved correctly.")
            seen, _ = await ask_or(self.rt, "track.step", prompt, StepSeen, NOT_JUDGED, frames,
                                   SEQUENCE_SYSTEM)
            camera_ok = seen.seen if seen.confidence >= CAMERA_MIN_CONFIDENCE else None
            return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                                 camera_ok=camera_ok, camera_confidence=seen.confidence,
                                 observation=seen.observation, clip_uri=frames[-1].uri)

        reference = self.references.get(step.name)
        pair = pair_image(reference, clip) if reference is not None else None
        if pair is not None:
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected under test: {step.expected_on_camera}.")
            seen = await self._seen(prompt, [pair], PAIR_SYSTEM)
        elif reference is not None:
            # Both frames reach Gemini prefixed with their label, so they must be labelled
            # differently: sent as two identical "[track camera]" images the model could not
            # tell which was the reference and confirmed a dark lamp.
            # truth goes along too: dropping it left the simulated plant comparing every later car
            # with nothing, so a dead lamp after the first passing car was never caught there
            first = Media(mime=reference.mime, data=reference.data, uri=reference.uri,
                          label=f"reference frame: a car that passed step {step.step}", truth=reference.truth)
            second = Media(mime=clip.mime, data=clip.data, uri=clip.uri,
                           label="frame under test: the car being checked now", truth=clip.truth)
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected in the frame under test: {step.expected_on_camera}.")
            seen = await self._seen(prompt, [first, second], REFERENCE_SYSTEM)
        else:
            prompt = (f"Step {step.step}: {step.command}.\n"
                      f"Expected on camera: {step.expected_on_camera}.\n"
                      f"Judge only what this frame shows. Do not assume the car behaved correctly.")
            seen = await self._seen(prompt, [clip], SYSTEM)
        camera_ok = seen.seen if seen.confidence >= CAMERA_MIN_CONFIDENCE else None
        if camera_ok and telemetry_ok and step.name not in self.references:
            # A step both sources agree on becomes the reference the next car is compared to.
            self.references[step.name] = clip
        return ChecklistItem(step=step.step, name=step.name, telemetry_ok=telemetry_ok,
                             camera_ok=camera_ok, camera_confidence=seen.confidence,
                             observation=seen.observation, clip_uri=clip.uri)

    async def _seen(self, prompt: str, media: list[Media], system: str) -> StepSeen:
        """Whether a still frame shows the step done: a decision first, the full answer if needed.

        As a decision (settings.decisions) Gemini answers yes or no and nothing else, its confidence
        the probability of that answer, read from the token's log-probability. The probabilities are
        honest where the full answer was not: on dataset v8-track a healthy car whose crop was framed
        closer failed at confidence 1.0 in full, and as a decision came back unsure (0.51) - a person
        looks, instead of the car going to rework. Lamp passes took 2.0 s against 3.3 s.
        """
        if not use_decisions(self.rt, "track.decide"):
            seen, _ = await ask_or(self.rt, "track.step", prompt, StepSeen, NOT_JUDGED, media, system)
            return seen
        try:
            d = await ask(self.rt, "track.decide", f"{prompt}\nIs the expected result present in the frame under test?",
                          SEEN, media, system)
        except Exception as e:          # no decision: the full answer, with its own safe fallback
            log.warning("track.decide unavailable (%s: %s); asking for the full answer", type(e).__name__, e)
            seen, _ = await ask_or(self.rt, "track.step", prompt, StepSeen, NOT_JUDGED, media, system)
            return seen
        # No sentence, for a pass or a failure: asking for one after a "no" doubled the time of every
        # failure (4.7 s against 2.0 s), and the report is written from the steps that failed anyway.
        return StepSeen(seen=d.choice == "yes", confidence=d.confidence,
                        observation=f"camera, as a decision: {d.choice} (p={d.confidence:.2f})")

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

    async def judge_clip(self, vin: str, step_no: int, clip: Media, telemetry: dict) -> ChecklistItem:
        """Judge one routine step from evidence already in hand, exactly as a run would.

        For callers holding their own frames, such as the evaluation harness. A reference set in
        `references` for the step is used, as it would be after a passing car.
        """
        [step] = routine_steps(ROUTINE, [step_no])
        reported = TrackStep(vin=vin, mission_id="judge-clip", routine=ROUTINE, step=step_no,
                             name=step.name, telemetry=telemetry)
        run = _Run(vin, [step], reported={step_no: reported}, clips={step_no: clip})
        return await self._judge(run, step)

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
