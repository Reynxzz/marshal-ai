import pytest

from marshal_ai.driving import (
    AssistChoose, CommandRejected, MissionClient, MissionDone, MissionGo, MissionHold,
    MissionResume, Status,
)
from marshal_ai.driving.fake import FakeDrivingSystem
from marshal_ai.events import InMemoryBus, Topic
from marshal_ai.plant import load_plant_map


@pytest.fixture
def setup():
    m = load_plant_map()
    fake = FakeDrivingSystem(m)
    bus = InMemoryBus()
    client = MissionClient(fake, m, bus)
    fake.spawn("VIN-214")
    events = []
    client.on_event(events.append)
    return fake, client, bus, events


async def run(fake, seconds, dt=0.5):
    for _ in range(int(seconds / dt)):
        await fake.tick(dt)


async def test_mission_reaches_destination(setup):
    fake, client, bus, events = setup
    await client.send(MissionGo(mission_id="M-1", vin="VIN-214",
                                waypoints=["inspection_gate", "test_track", "yard.Y7"]))
    await run(fake, 200)
    done = [e for e in events if isinstance(e, MissionDone)]
    assert len(done) == 1 and done[0].location == "yard.Y7"
    assert fake.cars["VIN-214"].location == "yard.Y7"
    assert bus.history[Topic.VEHICLE_STATUS]


async def test_status_is_one_hz(setup):
    fake, client, _, events = setup
    await run(fake, 10)
    assert len([e for e in events if isinstance(e, Status)]) == 10


async def test_hold_and_resume(setup):
    fake, client, _, events = setup
    await client.send(MissionGo(mission_id="M-1", vin="VIN-214", waypoints=["inspection_gate"]))
    await client.send(MissionHold(vin="VIN-214", reason="gate queue full"))
    await run(fake, 60)
    assert fake.cars["VIN-214"].location == "line_end"
    await client.send(MissionResume(vin="VIN-214"))
    await run(fake, 60)
    assert fake.cars["VIN-214"].location == "inspection_gate"


async def test_timed_hold_releases_itself(setup):
    fake, client, _, _ = setup
    await client.send(MissionGo(mission_id="M-1", vin="VIN-214", waypoints=["inspection_gate"]))
    await client.send(MissionHold(vin="VIN-214", duration_s=40, reason="spacing"))
    await run(fake, 30)
    assert fake.cars["VIN-214"].location == "line_end"
    await run(fake, 60)
    assert fake.cars["VIN-214"].location == "inspection_gate"


async def test_unknown_waypoint_rejected(setup):
    _, client, _, _ = setup
    with pytest.raises(CommandRejected):
        await client.send(MissionGo(mission_id="M-1", vin="VIN-214", waypoints=["moon_base"]))


async def test_assist_flow_only_accepts_offered_options_from_a_human(setup):
    fake, client, bus, _ = setup
    await client.send(MissionGo(mission_id="M-1", vin="VIN-214", waypoints=["yard.Y7"]))
    await run(fake, 5)
    await fake.raise_assist("VIN-214", "AC-1", "unknown object in lane",
                            ["wait", "pass_left_3kmh", "reroute_lane_c"])
    assert bus.history[Topic.ASSIST_REQUESTS][0]["case_id"] == "AC-1"
    stopped_at = fake.cars["VIN-214"].x
    await run(fake, 20)
    assert fake.cars["VIN-214"].x == stopped_at

    with pytest.raises(CommandRejected):
        await client.send(AssistChoose(case_id="AC-1", vin="VIN-214", option="drive_through",
                                       chosen_by="supervisor-1"))
    with pytest.raises(CommandRejected):
        await client.send(AssistChoose(case_id="AC-1", vin="VIN-214", option="wait", chosen_by=""))
    await client.send(AssistChoose(case_id="AC-1", vin="VIN-214", option="wait",
                                   chosen_by="supervisor-1"))
    assert "AC-1" not in client.open_requests
    await run(fake, 5)
    assert fake.cars["VIN-214"].x != stopped_at
