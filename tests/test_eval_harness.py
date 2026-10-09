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
         "expect_people": True, "expect_recommended": "wait_for_removal"},
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
                "recommended": "wait_for_removal", "confidence": 0.9}))


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
