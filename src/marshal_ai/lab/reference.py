"""Is a test fair? A careful reference driver must be able to pass it.

A test that no driving system could pass (a worker stepping out 4 m in front of a car doing 8 km/h,
or "max speed 0.1 km/h" on a route the car has to drive) measures nothing and would block every
release. Code decides this, not Gemini: the spec runs in the quick simulator with a reference
driver that sees 30 m in any light, reacts in 0.5 s and slows to walking pace (5 km/h) through the
scenario's zone, as a careful driver would past a blind spot. Criteria it fails are unachievable.

Used by the scenario generator (the errors go back to Gemini in the fix loop) and by the
adversarial search (unavoidable variations are skipped and reported, never added to the suite).
"""

from __future__ import annotations

from dataclasses import replace

from marshal_ai.lab.judge import judge
from marshal_ai.lab.sim import DriverProfile, simulate
from marshal_ai.lab.spec import ScenarioSpec, TimeOfDay
from marshal_ai.plant import PlantMap

CAREFUL_SPEED_KMH = 5.0

REFERENCE = DriverProfile(
    name="reference-careful-driver",
    detect_range_m={"noon": 30.0, "dusk": 30.0, "night": 30.0},
    latency_s={"noon": 0.5, "dusk": 0.5, "night": 0.5},
    tall_occluder_latency_s=0.2,
)


def reference_for(spec: ScenarioSpec, plant_map: PlantMap) -> DriverProfile:
    """The reference driver, slowing through the scenario's zone and wherever its actors stand."""
    zones = {spec.map_zone} | {plant_map.points[p].zone for a in spec.actors for p in (a.position, a.start)
                               if p and plant_map.has_point(p)}
    caps = {f"{z}@{t.value}": CAREFUL_SPEED_KMH for z in zones for t in TimeOfDay}
    return replace(REFERENCE, zone_speed_caps_kmh=caps)


def unfair_reasons(spec: ScenarioSpec, plant_map: PlantMap) -> list[str]:
    """The criteria even the reference driver fails, as sentences for Gemini and engineers."""
    try:
        metrics = simulate(spec, plant_map, reference_for(spec, plant_map)).metrics
    except Exception:                  # a spec the simulator cannot place is the validator's business
        return []
    failed = judge(spec, metrics).failed
    out = []
    for text, c in zip(spec.pass_criteria, spec.criteria()):
        if text in failed:
            value = metrics.get(c.metric)
            out.append(f"'{text}' is not achievable: a careful reference driver (sees 30 m, reacts in "
                       f"0.5 s, slows to {CAREFUL_SPEED_KMH:.0f} km/h here) also fails it ({c.metric} = {value})")
    return out
