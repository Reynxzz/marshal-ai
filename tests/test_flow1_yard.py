from datetime import timedelta

from marshal_ai.records import BuildSheet, Car, CarReport, CarState, Verdict


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


async def test_a_car_that_found_the_yard_full_gets_a_slot_once_one_frees(plant):
    """It used to wait where its report finished for good, and was never shipped."""
    app = plant([])
    slots = app.yard.slots_by_distance()
    for i, slot in enumerate(slots):
        vin = f"VIN-P{i}"
        app.rt.store.put(Car(vin=vin, build_sheet=BuildSheet(model="sedan", colour="blue", truck_id="T-07")))
        await park(app, vin, slot)
    late = Car(vin="VIN-LATE", build_sheet=BuildSheet(model="sedan", colour="blue", truck_id="T-08"),
               state=CarState.REPORT, location="report_point")
    app.rt.store.put(late)
    app.rt.driving.spawn("VIN-LATE", "report_point")
    app.rt.store.put(CarReport(vin="VIN-LATE", verdict=Verdict.PASS))
    await app.yard.tick()
    assert car(app, "VIN-LATE").yard_slot is None and app.yard.waiting_for_slot()[0].vin == "VIN-LATE"
    assert app.yard.ship("T-07")                      # the truck takes its cars and frees their slots
    await app.yard.tick()
    assert car(app, "VIN-LATE").yard_slot in slots and not app.yard.waiting_for_slot()


def _fill_yard(app, truck="T-07"):
    for i, slot in enumerate(app.yard.slots_by_distance()):
        vin = f"VIN-F{i}"
        app.rt.store.put(Car(vin=vin, build_sheet=BuildSheet(model="sedan", colour="blue", truck_id=truck),
                             state=CarState.YARD, location=slot, yard_slot=slot))
        app.rt.store.put(CarReport(vin=vin, verdict=Verdict.PASS))


def _passed_car(app, vin):
    app.rt.store.put(Car(vin=vin, build_sheet=BuildSheet(model="sedan", colour="blue", truck_id="T-08"),
                         state=CarState.REPORT, location="report_point"))
    app.rt.driving.spawn(vin, "report_point")
    app.rt.store.put(CarReport(vin=vin, verdict=Verdict.PASS))


def _yard_calls(app):
    return [c for c in app.rt.llm.calls if c.task == "yard.exception"]


async def test_a_full_yard_is_waited_out_without_asking_gemini(plant):
    app = plant([])
    _fill_yard(app)
    _passed_car(app, "VIN-W1")
    assert await app.yard.send_to_slot("VIN-W1") is None
    await app.yard.tick()
    assert not _yard_calls(app) and app.yard.waiting_for_slot()[0].vin == "VIN-W1"


async def test_a_backlog_for_slots_is_raised_once_as_the_line_outpacing_the_yard(plant):
    app = plant([])
    _fill_yard(app)
    for vin in ("VIN-W1", "VIN-W2", "VIN-W3"):
        _passed_car(app, vin)
    for _ in range(5):
        await app.yard.tick()
    calls = _yard_calls(app)
    assert len(calls) == 1 and "line_outpacing_yard" in calls[0].prompt
    assert app.yard.decisions[-1]["kind"] == "line_outpacing_yard"
    app.yard.ship("T-07")                          # the yard catches up
    for _ in range(3):
        await app.yard.tick()
    assert not app.yard.waiting_for_slot() and "line_outpacing_yard" not in app.yard.handled


async def test_a_re_slot_into_a_taken_slot_waits_for_that_car_to_move_out(plant):
    from marshal_ai.flows.yard import YardAction, YardPlan
    app = plant(["VIN-214", "VIN-231"])
    near = app.yard.slots_by_distance()
    await park(app, "VIN-214", near[0])
    await park(app, "VIN-231", near[-1])
    plan = YardPlan(summary="swap", reasoning="next truck first", actions=[
        YardAction(vin="VIN-231", action="go", waypoints=[near[0]], reason="next truck"),
        YardAction(vin="VIN-214", action="go", waypoints=[near[2]], reason="late truck")])
    await app.yard.execute(plan)
    # VIN-214 leaves first; VIN-231 is held until near[0] is free
    assert car(app, "VIN-214").mission_id is not None
    assert car(app, "VIN-231").mission_id is None and car(app, "VIN-231").yard_slot == near[-1]
    await app.run_for(120)
    assert car(app, "VIN-214").location == near[2]
    assert car(app, "VIN-231").location == near[0]


async def test_a_swap_inside_a_bigger_re_slot_goes_through_a_free_slot(plant):
    """P1 to P2's slot and P2 to P1's, with P3 moving too: each waited for the other for good."""
    from marshal_ai.flows.yard import YardAction, YardPlan
    app = plant(["VIN-214", "VIN-231", "VIN-240"])
    near = app.yard.slots_by_distance()
    await park(app, "VIN-214", near[0])
    await park(app, "VIN-231", near[1])
    await park(app, "VIN-240", near[2])
    plan = YardPlan(summary="swap", reasoning="next truck first", actions=[
        YardAction(vin="VIN-214", action="go", waypoints=[near[1]], reason="late truck"),
        YardAction(vin="VIN-231", action="go", waypoints=[near[0]], reason="next truck"),
        YardAction(vin="VIN-240", action="go", waypoints=[near[3]], reason="late truck")])
    await app.yard.execute(plan)
    first_stop = car(app, "VIN-214").yard_slot
    assert first_stop not in (near[0], near[1], near[2], near[3]), "it waits in a free slot"
    await app.run_for(300)
    assert car(app, "VIN-214").location == near[1]
    assert car(app, "VIN-231").location == near[0]
    assert car(app, "VIN-240").location == near[3]


async def test_a_plan_sending_uninspected_cars_to_shipping_slots_is_rejected(plant):
    from marshal_ai.flows.yard import YardAction, YardPlan
    app = plant(["VIN-214"])
    slot = app.yard.slots_by_distance()[0]
    plan = YardPlan(summary="clear the line", reasoning="backlog", actions=[
        YardAction(vin="VIN-214", action="go", waypoints=[slot], reason="make room")])
    errors = app.yard.check_plan(plan)
    assert errors and "PASS report" in errors[0]
