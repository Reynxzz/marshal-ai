"""The harness on a scripted model: does it run the flows' own code and score what comes back?"""

import json

import pytest

from marshal_ai.eval.dataset import Dataset, MediaCache, load_dataset
from marshal_ai.eval.harness import evaluate
from marshal_ai.llm import FakeLLM

# Frames are stand-ins: the scripted model answers from their bytes, as Gemini would from pixels.
FRAMES = {"lit.jpg": b"LIT", "dark.jpg": b"DARK", "car.jpg": b"BLUE SEDAN", "stuck.jpg": b"BOX AND WORKER"}

DATASET = {
    "name": "test",
    "gate": [
        {"id": "clean", "views": {v: "car.jpg" for v in ("front", "rear", "left", "right")},
         "build_sheet": {"model": "sedan", "colour": "blue"}, "expect_verdicts": ["PASS"]},
        {"id": "sheet-says-red", "views": {v: "car.jpg" for v in ("front", "rear", "left", "right")},
         "build_sheet": {"model": "sedan", "colour": "red"}, "expect_verdicts": ["FAIL", "REVIEW"],
         "expect_mismatch_items": ["colour"]},
    ],
    "track": [
        {"id": "lit", "step": 1, "frame": "lit.jpg", "telemetry": {"headlights": "ON"}, "expect_seen": True},
        {"id": "dark", "step": 1, "frame": "dark.jpg", "telemetry": {"headlights": "ON"}, "expect_seen": False},
        {"id": "dark-vs-ref", "step": 1, "frame": "dark.jpg", "reference": "lit.jpg",
         "telemetry": {"headlights": "ON"}, "expect_seen": False},
    ],
    "assist": [
        {"id": "box-and-worker", "frames": ["stuck.jpg"], "reason": "unknown object in lane",
         "options": ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
         "expect_people": True, "expect_recommended": "wait_for_removal",
         "expect_objects": [{"kind": "obstacle", "box_2d": [500, 400, 700, 600]},
                            {"kind": "person", "box_2d": [100, 100, 200, 200]}]},
    ],
}


@pytest.fixture
def dataset(tmp_path):
    for name, data in FRAMES.items():
        (tmp_path / name).write_bytes(data)
    path = tmp_path / "test.json"
    path.write_text(json.dumps(DATASET))
    return load_dataset(path)


def honest_model(confirm_dark: bool = False) -> FakeLLM:
    def variant(call):
        red = '"colour":"red"' in call.prompt.replace(" ", "")
        mismatches = [{"item": "colour", "expected": "red", "observed": "blue", "confidence": 0.95}] if red else []
        return {"observed_colour": "blue", "mismatches": mismatches, "confidence": 0.95}

    def track(call):
        frame = call.media[-1].data              # with a reference, the frame under test comes last
        lit = frame == b"LIT" or (confirm_dark and frame == b"DARK")
        return {"seen": lit, "confidence": 0.95, "observation": "lit" if lit else "dark"}

    return (FakeLLM()
            .on("visual.defects", lambda call: {"findings": [], "clear_confidence": 0.95})
            .on("visual.variant", variant)
            .on("track.step", track)
            .on("assist.assess", lambda call: {
                "situation": "box in lane, worker beside it", "people_or_moving_equipment": True,
                "ranking": [{"option": "wait_for_removal", "reason": "a person is near", "risk": "low"},
                            {"option": "pass_left_3kmh", "reason": "passes the worker", "risk": "high"},
                            {"option": "reroute_lane_c", "reason": "longer", "risk": "medium"}],
                "recommended": "wait_for_removal", "confidence": 0.9})
            .on("assist.locate", lambda call: {"objects": [
                {"label": "box", "kind": "obstacle", "frame": 0, "box_2d": [500, 400, 700, 600]},
                {"label": "worker", "kind": "person", "frame": 0, "box_2d": [300, 650, 700, 720]}]}))


async def test_a_correct_model_scores_full_marks(dataset):
    data, base = dataset
    report = await evaluate(data, base, honest_model(), runs=2)

    gate = report["flows"]["gate"]["metrics"]
    assert gate["accuracy"] == 1.0 and gate["first_pass_yield"] == 1.0
    assert gate["false_alarm_rate"] == 0.0 and gate["mismatch_recall"] == 1.0
    assert gate["consistency"] == 1.0
    track = report["flows"]["track"]["metrics"]
    assert track["accuracy"] == 1.0 and track["false_confirm_rate"] == 0.0
    assist = report["flows"]["assist"]["metrics"]
    assert assist["recommendation_accuracy"] == 1.0 and assist["high_risk_recommended"] == 0
    assert assist["object_recall"] == 1.0 and assist["within_budget"] == 1.0
    assert assist["box_hit_rate"] == 0.5            # the box is on the box; the worker's box is elsewhere
    assert report["flows"]["gate"]["cases"]["clean"]["outcomes"][0]["verdict"] == "PASS"


async def test_a_dark_lamp_confirmed_as_lit_is_counted_as_the_failure_it_is(dataset):
    data, base = dataset
    report = await evaluate(data, base, honest_model(confirm_dark=True), flows=("track",), runs=1)

    track = report["flows"]["track"]["metrics"]
    assert track["false_confirm_rate"] == 1.0          # both dark cases called lit
    assert track["false_reject_rate"] == 0.0
    assert track["accuracy"] == pytest.approx(1 / 3, abs=0.001)


async def test_the_reference_frame_reaches_the_model_first(dataset):
    data, base = dataset
    model = honest_model()
    await evaluate(data, base, model, flows=("track",), runs=1)

    with_reference = [c for c in model.calls if len(c.media) == 2]
    assert len(with_reference) == 1
    assert [m.data for m in with_reference[0].media] == [b"LIT", b"DARK"]


async def test_a_model_that_never_answers_leaves_the_decisions_to_people(dataset):
    data, base = dataset
    report = await evaluate(data, base, FakeLLM(), runs=1)      # no handlers: every call fails

    gate = report["flows"]["gate"]["metrics"]
    assert gate["first_pass_yield"] == 0.0 and gate["review_share"] == 1.0
    assert report["flows"]["track"]["metrics"]["unconfirmed_rate"] == 1.0
    assert report["flows"]["assist"]["metrics"]["high_risk_recommended"] == 0


async def test_only_the_requested_flows_run(dataset):
    data, base = dataset
    report = await evaluate(data, base, honest_model(), flows=("assist",), runs=1)

    assert list(report["flows"]) == ["assist"]


def test_gs_media_are_downloaded_once(tmp_path):
    downloads = []

    class Blob:
        def __init__(self, name):
            self.name = name

        def download_to_filename(self, path):
            downloads.append(self.name)
            open(path, "wb").write(b"jpeg")

    class Client:
        def bucket(self, _):
            return type("B", (), {"blob": lambda self, name: Blob(name)})()

    cache = MediaCache(tmp_path, cache=tmp_path / "cache", client=Client())
    assert cache.read("gs://bucket/eval/v1/a.jpg") == b"jpeg"
    assert cache.read("gs://bucket/eval/v1/a.jpg") == b"jpeg"
    assert downloads == ["eval/v1/a.jpg"]


def test_the_shipped_dataset_loads():
    data, _ = load_dataset("eval/datasets/v1.json")
    assert isinstance(data, Dataset)
    assert {c.id for c in data.gate} >= {"s1-clean", "s1-sheet-says-red"}
    assert all(c.frame.startswith("gs://") for c in data.track)


def damage_model() -> FakeLLM:
    """Sees a scratch on the rear left door in DENTED photos and nothing in CLEAN ones."""
    def defects(call):
        if call.media[0].data == b"DENTED":
            return {"findings": [{"zone": "rear_left_door", "type": "paint scratch", "confidence": 0.93}],
                    "clear_confidence": 0.9}
        return {"findings": [], "clear_confidence": 0.95}
    return FakeLLM().on("visual.defects", defects)


async def test_damage_photos_are_scored_for_recall_kind_zone_and_false_alarms(tmp_path):
    (tmp_path / "dented.jpg").write_bytes(b"DENTED")
    (tmp_path / "clean.jpg").write_bytes(b"CLEAN")
    (tmp_path / "d.json").write_text(json.dumps({"name": "d", "damage": [
        {"id": "scratched-door", "image": "dented.jpg", "expect_damaged": True,
         "expect_kinds": ["scratch"], "expect_zones": ["rear_left_door", "rear_right_door"]},
        {"id": "dent-on-bonnet", "image": "dented.jpg", "expect_damaged": True,
         "expect_kinds": ["dent"], "expect_zones": ["bonnet"]},
        {"id": "clean-hood", "image": "clean.jpg", "expect_damaged": False},
    ]}))
    data, base = load_dataset(tmp_path / "d.json")

    m = (await evaluate(data, base, damage_model(), flows=("damage",), runs=2))["flows"]["damage"]["metrics"]

    assert m["damage_recall"] == 1.0 and m["damage_failed"] == 1.0
    assert m["kind_precision"] == 0.5            # a scratch is right on the door, wrong for the dent
    assert m["zone_accuracy"] == 0.5
    assert m["recall_by_kind"] == {"dent": 0.0, "scratch": 1.0}
    assert m["false_alarm_rate"] == 0.0 and m["clean_pass_rate"] == 1.0


def test_finding_types_read_as_damage_kinds():
    from marshal_ai.eval.damage import kinds_of
    assert kinds_of("scratch") == {"scratch"}
    assert kinds_of("paint_scratch") == {"scratch", "paint"}
    assert kinds_of("dent") == {"dent"}
    assert kinds_of("missing_part") == {"missing_part"}
    assert kinds_of("cracked bumper") == {"broken_part"}
    assert kinds_of("panel_gap") == set()


def test_box_overlap():
    from marshal_ai.eval.metrics import iou
    assert iou([0, 0, 100, 100], [0, 0, 100, 100]) == 1.0
    assert iou([0, 0, 100, 100], [0, 100, 100, 200]) == 0.0
    assert iou([0, 0, 100, 100], [0, 50, 100, 150]) == pytest.approx(1 / 3)
    assert iou(None, [0, 0, 1, 1]) == 0.0


async def test_a_rework_plan_is_scored_before_and_after_the_router_checks_it():
    """A model that puts paint before the brake light and writes the scratch first: the
    supervisor still gets the right plan, but the scores say whose work that was."""
    data, base = load_dataset("eval/datasets/v3-report.json")
    data.report = [c for c in data.report if c.id == "brake-light-and-scratch"]
    paint_first = FakeLLM().on("report.write", lambda call: {
        "summary": "A scratch on the rear left door. The left brake light is dark.",
        "steps": [{"bay": "paint", "defect_type": "scratch", "reason": "scratch on the door"},
                  {"bay": "electrical", "defect_type": "brake_light", "reason": "dark lamp"}]})

    m = (await evaluate(data, base, paint_first, flows=("report",), runs=1))["flows"]["report"]["metrics"]

    assert m["bays_right"] == 1.0                 # the router put electrical first
    assert m["gemini_bays_right"] == 1.0 and m["gemini_safety_first"] == 0.0
    assert m["summary_safety_first"] == 0.0 and m["summary_names_both"] == 1.0


def test_the_report_dataset_loads():
    data, _ = load_dataset("eval/datasets/v3-report.json")
    assert len(data.report) == 6


async def test_a_generated_test_is_scored_for_whether_it_tests_the_incident():
    """The offline model's spec is valid; the score still says where it strays from the case."""
    from marshal_ai.offline import offline_llm
    data, base = load_dataset("eval/datasets/v4-scenario.json")
    data.scenario = [c for c in data.scenario if c.id == "design-sc0142"]
    m = (await evaluate(data, base, offline_llm(), flows=("scenario",), runs=1))["flows"]["scenario"]["metrics"]
    assert m["generated"] == 1.0 and m["first_try"] == 1.0
    assert m["faithful"] == 1.0


def test_an_actor_where_the_car_starts_is_rejected_with_the_fix():
    from marshal_ai.lab.generator import INCIDENT_EXAMPLE_SPEC
    from marshal_ai.lab.reference import unfair_reasons
    from marshal_ai.lab.validate import validate_spec
    from marshal_ai.plant.map import load_plant_map
    plant = load_plant_map()
    good = validate_spec(INCIDENT_EXAMPLE_SPEC, plant)
    assert good.ok and unfair_reasons(good.spec, plant) == []
    bad = dict(INCIDENT_EXAMPLE_SPEC, ego={"route": "test_track->report_point", "speed_kmh": 8})
    errors = validate_spec(bad, plant).errors
    assert len(errors) == 2 and all("is where the car starts" in e for e in errors)


async def test_a_search_is_scored_against_random_search_on_the_same_scenario():
    from marshal_ai.offline import offline_llm
    data, base = load_dataset("eval/datasets/v5-search.json")
    data.search = [c.model_copy(update={"random_seeds": 2}) for c in data.search if c.id == "design-sc0142"]
    report = await evaluate(data, base, offline_llm(), flows=("search",), runs=1)
    m = report["flows"]["search"]["metrics"]
    [outcome] = report["flows"]["search"]["cases"]["design-sc0142"]["outcomes"]
    assert outcome["found"] and outcome["runs_to_failure"] <= 60 and len(outcome["random"]) == 2
    assert m["model"]["found"] == 1.0 and m["explained"] == 1.0
    assert m["fix_proposed"] == 1.0 and m["fix_works"] == 1.0
    assert set(m["random"]) == set(m["model"])
