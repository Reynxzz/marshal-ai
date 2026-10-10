"""Run one case through the flow's own code, the same path a real run takes, and record the answer.

Each run gets a fresh app with an in-memory store and the fake plant, so nothing reaches
Firestore and no CARLA server is needed: the evidence comes from the case, not from cameras.
"""

from __future__ import annotations

import time
from typing import Callable

from marshal_ai.app import MarshalApp
from marshal_ai.config import Settings
from marshal_ai.driving.messages import AssistRequest
from marshal_ai.driving.routines import routine_steps
from marshal_ai.eval.damage import kinds_of
from marshal_ai.eval.dataset import AssistCase, DamageCase, GateCase, MediaCache, TrackCase
from marshal_ai.flows.functional import ROUTINE
from marshal_ai.llm import LLM, Media
from marshal_ai.records import Car, CarState
from marshal_ai.runtime import build_runtime

ISOLATED = Settings(bus="memory", store="memory", history="none", artifacts="none", driving="fake",
                    llm="offline", lab="sim")


class _QuietBus:
    """Delivers nothing. A judgement is scored on its own; left wired, a PASS at the gate set off
    the report flow, which tried to send the evaluation car to the track and failed."""

    async def publish(self, topic, message) -> None:
        pass

    def subscribe(self, topic, handler) -> None:
        pass


def fresh_app(llm: LLM) -> MarshalApp:
    rt = build_runtime(ISOLATED, llm=llm)
    rt.bus = _QuietBus()
    return MarshalApp(rt)


def _jpeg(cache: MediaCache, ref: str, label: str) -> Media:
    return Media(mime="image/jpeg", data=cache.read(ref), uri=ref if ref.startswith("gs://") else None,
                 label=label)


async def _timed(fn: Callable) -> tuple[object, float]:
    start = time.perf_counter()
    out = await fn()
    return out, time.perf_counter() - start


async def run_gate(llm: LLM, case: GateCase, cache: MediaCache) -> dict:
    app = fresh_app(llm)
    vin = f"EVAL-{case.id}"
    app.rt.store.put(Car(vin=vin, build_sheet=case.build_sheet, state=CarState.INSPECTION_GATE,
                         location="inspection_gate"))
    # The gate reads the view name from the start of each label, as the cameras write it.
    images = [_jpeg(cache, ref, f"{view} camera") for view, ref in case.views.items()]
    result, seconds = await _timed(lambda: app.visual.inspect(vin, images=images))
    flagged = [m.split(":", 1)[0].strip().lower() for m in result.build_sheet_mismatches]
    return {"verdict": result.verdict.value,
            "findings": [f.model_dump(mode="json", exclude={"image"}) for f in result.findings],
            "mismatch_items": flagged,
            "mismatches": result.build_sheet_mismatches,
            "observations": result.observations,
            "seconds": round(seconds, 2)}


async def run_track(llm: LLM, case: TrackCase, cache: MediaCache) -> dict:
    app = fresh_app(llm)
    vin = f"EVAL-{case.id}"
    clip = _jpeg(cache, case.frame, f"track camera, step {case.step}")
    if case.reference is not None:
        # As if a car that passed this step had gone first: its frame becomes the reference.
        [step] = routine_steps(ROUTINE, [case.step])
        app.functional.references[step.name] = _jpeg(cache, case.reference, "reference")
    item, seconds = await _timed(lambda: app.functional.judge_clip(vin, case.step, clip, case.telemetry))
    return {"camera_ok": item.camera_ok, "camera_confidence": item.camera_confidence,
            "telemetry_ok": item.telemetry_ok, "passed": item.passed,
            "observation": item.observation, "seconds": round(seconds, 2)}


async def run_assist(llm: LLM, case: AssistCase, cache: MediaCache) -> dict:
    app = fresh_app(llm)
    vin = f"EVAL-{case.id}"
    app.rt.store.put(Car(vin=vin, build_sheet={"model": "sedan", "colour": "black"},
                         state=CarState.LINE_END, location=case.location or "line_end"))
    frames = [_jpeg(cache, ref, f"frame {i + 1} of {len(case.frames)} before the stop")
              for i, ref in enumerate(case.frames)]
    # The desk asks the driving system for the frames it held when the car stopped; here they
    # come from the case instead.
    app.assistance.clip = lambda request: frames
    request = AssistRequest(case_id=f"AC-{case.id}", vin=vin, location=case.location,
                            reason=case.reason, options=case.options)
    result, seconds = await _timed(lambda: app.assistance.on_request(request))
    risk = {r["option"]: r.get("risk") for r in result.ranking}
    return {"recommended": result.recommended,
            "people_or_moving_equipment": result.people_or_moving_equipment,
            "recommended_risk": risk.get(result.recommended),
            "confidence": result.confidence, "situation": result.situation,
            "objects": [{"label": o["label"], "kind": o["kind"], "frame": o["frame"],
                         "box_2d": o["box_2d"]} for o in result.objects],
            "seconds": round(seconds, 2)}


async def run_damage(llm: LLM, case: DamageCase, cache: MediaCache) -> dict:
    app = fresh_app(llm)
    photo = _jpeg(cache, case.image, "inspection photo")
    (findings, clear, verdict), seconds = await _timed(lambda: app.visual.assess_photo(photo))
    return {"verdict": verdict.value,
            "findings": [{"zone": f.zone, "type": f.type, "confidence": f.confidence,
                          "kinds": sorted(kinds_of(f.type))} for f in findings],
            "clear_confidence": clear, "seconds": round(seconds, 2)}


RUNNERS = {"gate": run_gate, "track": run_track, "assist": run_assist, "damage": run_damage}
