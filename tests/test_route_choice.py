"""Route choice (flows/route.py): Gemini picks the way to the yard when something is going on."""

from dataclasses import replace

from marshal_ai.flows import route
from marshal_ai.llm import LLMError
from marshal_ai.records import AssistanceCase, Car, CarReport, Verdict


def ready(app, vin="VIN-214"):
    """A car that has passed, standing at the report point."""
    c = app.rt.store.get(Car, vin)
    app.rt.store.put(c.model_copy(update={"location": "report_point"}))
    app.rt.store.put(CarReport(vin=vin, verdict=Verdict.PASS))
    return vin


def calls(app, task="route.choose"):
    return [c for c in app.rt.llm.calls if c.task == task]


def test_the_map_has_two_ways_from_the_report_point_to_the_yard(app):
    ways = app.rt.map.routes("report_point", "yard.Y4")
    assert len(ways) == 2
    assert "finishing_corner.p1" in ways[0] and "lane_c" in ways[1]       # the corner is shorter
    assert app.rt.map.route_length(ways[0]) < app.rt.map.route_length(ways[1])


async def test_nothing_going_on_takes_the_shortest_way_without_asking(plant):
    app = plant(["VIN-214"])
    slot = await app.yard.send_to_slot(ready(app))
    c = app.rt.store.get(Car, "VIN-214")
    assert c.route_by == "rules" and "finishing_corner.p1" in c.route and c.route[-1] == slot
    assert not calls(app)
    assert app.yard.route_log[-1]["why"].startswith("nothing reported")


async def test_a_forklift_in_the_corner_sends_the_car_along_lane_c(plant):
    app = plant(["VIN-214"])
    app.rt.plant.hazards["finishing_corner"] = "forklift unloading pallets across the road"
    await app.yard.send_to_slot(ready(app))
    c = app.rt.store.get(Car, "VIN-214")
    assert c.route_by == "gemini" and "lane_c" in c.route and "finishing_corner.p1" not in c.route
    call = calls(app)[-1]
    assert "forklift unloading pallets" in call.prompt and "via lane_c" in call.prompt
    record = app.yard.route_log[-1]
    assert record["choice"] == "via lane_c" and record["probs"]["via lane_c"] > 0.5
    assert app.yard.kpis()["routes_chosen_by_gemini"] == 1


async def test_a_car_stopped_on_the_corner_counts_as_a_reason(plant):
    app = plant(["VIN-214", "VIN-223"])
    app.rt.store.put(AssistanceCase(case_id="A-1", vin="VIN-223", location="finishing_corner.p2",
                                    options=["wait_for_removal"]))
    await app.yard.send_to_slot(ready(app))
    c = app.rt.store.get(Car, "VIN-214")
    assert "lane_c" in c.route
    assert "VIN-223 at finishing_corner.p2" in calls(app)[-1].prompt


async def test_a_closed_zone_is_struck_by_the_rules_before_gemini_is_asked(plant):
    app = plant(["VIN-214"])
    app.rt.plant.closed_zones.add("finishing_corner")
    app.rt.plant.hazards["finishing"] = "people crossing"             # on both ways, so a reason
    await app.yard.send_to_slot(ready(app))
    c = app.rt.store.get(Car, "VIN-214")
    assert c.route_by == "rules" and "lane_c" in c.route and not calls(app)
    assert app.yard.route_log[-1]["why"] == "the only open way"


async def test_a_failed_call_keeps_the_shortest_way(plant):
    app = plant(["VIN-214"])
    app.rt.plant.hazards["finishing_corner"] = "forklift working"

    def down(call):
        raise LLMError("unavailable")
    app.rt.llm.on("route.choose", down)
    await app.yard.send_to_slot(ready(app))
    c = app.rt.store.get(Car, "VIN-214")
    assert c.route_by == "rules" and "finishing_corner.p1" in c.route


async def test_always_mode_asks_even_when_nothing_is_going_on(plant):
    app = plant(["VIN-214"])
    app.rt.settings = replace(app.rt.settings, route="always")
    await app.yard.send_to_slot(ready(app))
    assert len(calls(app)) == 1
    assert "finishing_corner.p1" in app.rt.store.get(Car, "VIN-214").route   # quiet: shortest


async def test_the_next_car_sees_the_first_cars_way(plant):
    app = plant(["VIN-214", "VIN-223"])
    app.rt.settings = replace(app.rt.settings, route="always")
    await app.yard.send_to_slot(ready(app, "VIN-214"))
    await app.yard.send_to_slot(ready(app, "VIN-223"))
    prompt = calls(app)[-1].prompt
    assert "cars driving on it: VIN-214" in prompt


async def test_the_car_drives_the_chosen_way_to_its_slot(plant):
    app = plant(["VIN-214"])
    app.rt.plant.hazards["finishing_corner"] = "forklift working"
    await app.yard.send_to_slot(ready(app))
    await app.run_for(200)
    c = app.rt.store.get(Car, "VIN-214")
    assert c.location == c.yard_slot and "VIN-214" not in app.yard.routes_in_use


def test_offline_stand_in_reads_the_prompt():
    from marshal_ai.llm import LLMCall
    text = route.prompt("V", "report_point", "yard.Y4", [
        route.Way(["a", "b", "c"], 110, ["finishing_corner"], "via finishing_corner", hazards=["finishing_corner: forklift"]),
        route.Way(["a", "d", "c"], 123, ["finishing"], "via lane_c"),
    ], 10.0)
    assert route.offline_choice(LLMCall("route.choose", text, [], ""))["choice"] == "via lane_c"


async def test_a_car_being_routed_is_not_sent_again_by_the_tick(plant):
    import asyncio
    app = plant(["VIN-214"])
    app.rt.plant.hazards["finishing_corner"] = "forklift working"
    slow = app.rt.llm.handlers["route.choose"]

    def wait_then_answer(call):
        import time
        time.sleep(0.2)
        return slow(call)
    app.rt.llm.on("route.choose", wait_then_answer)
    ready(app)
    await asyncio.gather(app.yard.send_to_slot("VIN-214"), app.yard.tick(), app.yard.tick())
    assert len(calls(app)) == 1 and len(app.yard.route_log) == 1
