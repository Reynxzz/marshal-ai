from datetime import timedelta

from marshal_ai.records import Car, CarReport, CarState, Verdict


def car(app, vin):
    return app.rt.store.get(Car, vin)


async def test_release_rules_space_cars_and_limit_the_gate_queue(plant):
    app = plant(["VIN-214", "VIN-223", "VIN-240"])
    await app.tick()
    assert car(app, "VIN-214").state == CarState.INSPECTION_GATE
    assert car(app, "VIN-223").state == CarState.LINE_END           # 25 m spacing not yet met
    await app.run_for(20)
    assert car(app, "VIN-223").state == CarState.INSPECTION_GATE
    ok, why = app.yard.can_release()
    assert not ok


async def test_clean_car_goes_through_to_a_slot_near_the_loading_bay_and_ships(plant):
    app = plant(["VIN-214"])
    await app.run_for(400)
    c = car(app, "VIN-214")
    assert c.state == CarState.YARD
    assert c.yard_slot == app.yard.slots_by_distance()[0]           # T-07 is the next truck
    assert app.rt.store.get(CarReport, "VIN-214").verdict == Verdict.PASS
    assert app.yard.ship("T-07") == ["VIN-214"]
    assert car(app, "VIN-214").state == CarState.SHIPPED


async def test_car_without_pass_report_cannot_go_to_a_shipping_slot_or_ship(plant):
    app = plant(["VIN-214"])
    try:
        await app.yard.send_to_slot("VIN-214")
        raise AssertionError("expected refusal")
    except ValueError:
        pass
    app.rt.store.put(car(app, "VIN-214").model_copy(update={"state": CarState.YARD}))
    assert app.yard.ship("T-07") == []


async def park(app, vin, slot):
    app.rt.driving.spawn(vin, slot)
    c = car(app, vin)
    app.rt.store.put(c.model_copy(update={"state": CarState.YARD, "location": slot, "yard_slot": slot}))
    app.rt.store.put(CarReport(vin=vin, verdict=Verdict.PASS))


async def test_late_truck_re_slots_cars_after_supervisor_approval(plant):
    app = plant(["VIN-214", "VIN-231", "VIN-240"])
    near = app.yard.slots_by_distance()
    await park(app, "VIN-214", near[0])       # T-07
    await park(app, "VIN-231", near[-1])      # T-08
    await park(app, "VIN-240", near[-2])      # T-08
    truck = app.rt.plant.trucks["T-07"]
    truck.eta = truck.eta + timedelta(minutes=40)
    await app.yard.tick()
    decision = app.yard.decisions[-1]
    assert decision["kind"] == "truck_delayed" and decision["status"] == "proposed"
    before = {v: car(app, v).yard_slot for v in ("VIN-231", "VIN-240")}
    result = await app.yard.decide(decision["proposal_id"], approve=True, by="supervisor-ana")
    assert set(result["moved"]) == {"VIN-231", "VIN-240"}
    await app.run_for(60)
    for vin in ("VIN-231", "VIN-240"):
        assert near.index(car(app, vin).yard_slot) < near.index(before[vin])
        assert car(app, vin).location == car(app, vin).yard_slot


async def test_closed_track_holds_cars_at_line_end(plant):
    app = plant(["VIN-214", "VIN-223"])
    app.rt.plant.closed_zones.add("test_track")
    await app.run_for(30)
    assert car(app, "VIN-214").state == CarState.LINE_END
    assert app.yard.decisions[-1]["kind"] == "track_closed"
    assert app.yard.decisions[-1]["status"] == "proposed"         # zone-level: supervisor approves
    app.rt.plant.closed_zones.discard("test_track")
    await app.run_for(5)
    assert car(app, "VIN-214").state == CarState.INSPECTION_GATE


async def test_line_outpacing_yard_slows_releases_after_approval(plant):
    app = plant(["VIN-214", "VIN-223"])
    app.rt.plant.line_rate_per_h, app.rt.plant.yard_rate_per_h = 40, 30
    await app.yard.tick()
    decision = app.yard.decisions[-1]
    assert decision["kind"] == "line_outpacing_yard"
    assert app.yard.release_interval_s is None
    await app.yard.decide(decision["proposal_id"], approve=True, by="supervisor-ana")
    assert app.yard.release_interval_s == 120.0


async def test_plan_with_unknown_point_is_rejected_not_sent(app):
    from marshal_ai.flows.yard import YardAction, YardPlan
    plan = YardPlan(summary="x", reasoning="y", actions=[
        YardAction(vin="VIN-999", action="go", waypoints=["moon"], reason="z")])
    assert app.yard.check_plan(plan)
