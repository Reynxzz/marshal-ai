"""Validate a scenario spec against its schema and the plant map.

Errors are short, specific sentences: they go back to Gemini in the fix loop
(capped at 3 attempts) and then to an engineer if it still fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from marshal_ai.lab.spec import OCCLUDERS, ActorType, ScenarioSpec
from marshal_ai.plant import PlantMap


@dataclass
class SpecCheck:
    spec: ScenarioSpec | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.spec is not None and not self.errors


def _schema_errors(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        where = ".".join(str(p) for p in err["loc"]) or "spec"
        out.append(f"{where}: {err['msg']}")
    return out


def validate_spec(raw: dict, plant_map: PlantMap) -> SpecCheck:
    try:
        spec = ScenarioSpec.model_validate(raw)
    except ValidationError as e:
        return SpecCheck(errors=_schema_errors(e))

    errors: list[str] = []
    if not plant_map.has_zone(spec.map_zone):
        errors.append(f"map_zone: unknown zone {spec.map_zone!r}; known zones: {sorted(plant_map.zones)}")

    route = spec.ego.waypoints
    for p in route:
        if not plant_map.has_point(p):
            errors.append(f"ego.route: unknown map point {p!r}")
    if all(plant_map.has_point(p) for p in route):
        for a, b in zip(route, route[1:]):
            try:
                plant_map.route(a, b)
            except ValueError:
                errors.append(f"ego.route: no drivable path from {a} to {b}")

    present = {a.type for a in spec.actors}
    touched_zones: set[str] = set()
    for i, actor in enumerate(spec.actors):
        where = f"actors.{i}"
        # The car spawns at the route's first point, so an actor there starts inside it: contact
        # at t=0, which no driver could avoid. Gemini did this on every try at scene 3's incident
        # and, told only that "no_contact is not achievable", loosened the criteria instead.
        for field_name in ("position", "start"):
            if getattr(actor, field_name) == route[0]:
                errors.append(f"{where}.{field_name}: {route[0]} is where the car starts, so it would start "
                              f"inside this {actor.type.value}; start the route at an earlier point that "
                              f"leads to {route[0]}, or place the {actor.type.value} further along the route")
        if actor.position is None and actor.start is None:
            errors.append(f"{where}: needs a position or a start")
        if actor.position is not None:
            if plant_map.has_point(actor.position):
                touched_zones.add(plant_map.points[actor.position].zone)
            else:
                errors.append(f"{where}.position: unknown map point {actor.position!r}")
        if actor.start is not None:
            if actor.start.startswith("behind:"):
                target = actor.start.removeprefix("behind:")
                if target not in {o.value for o in OCCLUDERS}:
                    errors.append(f"{where}.start: can only start behind one of "
                                  f"{sorted(o.value for o in OCCLUDERS)}, not {target!r}")
                elif ActorType(target) not in present:
                    errors.append(f"{where}.start: behind:{target} but the scenario has no {target}")
            elif plant_map.has_point(actor.start):
                touched_zones.add(plant_map.points[actor.start].zone)
            else:
                errors.append(f"{where}.start: unknown map point {actor.start!r}")
        if actor.type == ActorType.WORKER and actor.state in ("moving", "crossing") \
                and actor.walk_speed_ms is None:
            errors.append(f"{where}: a moving worker needs walk_speed_ms")
        if actor.type == ActorType.WORKER and actor.start and actor.start.startswith("behind:") \
                and actor.walk_speed_ms is None:
            errors.append(f"{where}: a worker stepping out needs walk_speed_ms")
        if actor.type != ActorType.WORKER and actor.walk_speed_ms is not None:
            errors.append(f"{where}: walk_speed_ms is only for workers")
        if actor.type == ActorType.BOX and (actor.position is None or actor.state not in ("stationary", "parked")):
            errors.append(f"{where}: a box is a stationary object with a position on the ego route")

    if plant_map.has_zone(spec.map_zone):
        route_zones = {plant_map.points[p].zone for p in route if plant_map.has_point(p)}
        path_zones = set(route_zones)
        if all(plant_map.has_point(p) for p in route):
            try:
                for a, b in zip(route, route[1:]):
                    path_zones |= {plant_map.points[p].zone for p in plant_map.route(a, b)}
            except ValueError:
                pass
        if spec.map_zone not in path_zones | touched_zones:
            errors.append(f"map_zone: ego never passes {spec.map_zone} and no actor is placed there")

    if ActorType.BOX in present and "reached_goal" in spec.pass_criteria:
        errors.append("pass_criteria: a box blocks the lane, so the car must stop; do not require reached_goal")

    uses_person_metric = any(c.metric in ("min_gap_to_person_m", "speed_near_person_kmh")
                             for c in spec.criteria())
    if uses_person_metric and ActorType.WORKER not in present:
        errors.append("pass_criteria: person metrics need at least one worker actor")

    return SpecCheck(spec=spec, errors=errors)
