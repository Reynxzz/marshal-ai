"""Demo plant matching the five-minute script.

VIN-214  clean car: passes the gate and track, goes to the yard
VIN-223  dead left brake lamp: telemetry says ON, the camera sees it off
VIN-231  wrong colour: build sheet white, car painted red
VIN-240  gets stuck at a box in the lane with a worker nearby
VIN-245  6 cm scratch on the rear left door: goes to human review
"""

from __future__ import annotations

import copy
from datetime import timedelta

from marshal_ai.fakeworld import CarTruth, Defect
from marshal_ai.plant.state import Truck
from marshal_ai.records import BuildSheet, Car
from marshal_ai.runtime import Runtime

BADGE = ["rear_badge"]

DEMO_CARS: list[tuple[Car, CarTruth]] = [
    (Car(vin="VIN-214", build_sheet=BuildSheet(model="sedan", colour="blue", options=BADGE, truck_id="T-07")),
     CarTruth(colour="blue", fitted=BADGE)),
    (Car(vin="VIN-223", build_sheet=BuildSheet(model="sedan", colour="blue", options=BADGE, truck_id="T-07")),
     CarTruth(colour="blue", fitted=BADGE, dead_lamps={"brake_left"})),
    (Car(vin="VIN-231", build_sheet=BuildSheet(model="sedan", colour="white", options=BADGE, truck_id="T-08")),
     CarTruth(colour="red", fitted=BADGE)),
    (Car(vin="VIN-240", build_sheet=BuildSheet(model="sedan", colour="grey", options=BADGE, truck_id="T-08")),
     CarTruth(colour="grey", fitted=BADGE)),
    (Car(vin="VIN-245", build_sheet=BuildSheet(model="sedan", colour="blue", options=BADGE, truck_id="T-07")),
     CarTruth(colour="blue", fitted=BADGE, defects=[Defect("rear_left_door", "scratch", 6.0)])),
]

BOX_SCENE = {"object": "cardboard box", "where": "in lane near finishing", "distance_m": 4,
             "people": [{"role": "worker", "distance_m": 3}]}


def seed_demo(rt: Runtime, vins: list[str] | None = None) -> None:
    now = rt.clock()
    rt.plant.trucks.setdefault("T-07", Truck("T-07", now + timedelta(minutes=30), now + timedelta(minutes=30)))
    rt.plant.trucks.setdefault("T-08", Truck("T-08", now + timedelta(minutes=50), now + timedelta(minutes=50)))
    spawn = getattr(rt.driving, "spawn", None)
    for car, truth in DEMO_CARS:
        if vins is not None and car.vin not in vins:
            continue
        if rt.store.get(Car, car.vin) is not None:
            continue
        rt.store.put(car.model_copy(update={"updated_at": now}))
        if rt.world is not None:
            rt.world.add(car.vin, copy.deepcopy(truth))
        if spawn is not None and rt.settings.driving in ("fake", "metadrive"):
            spawn(car.vin, car.location)
