"""Evaluation cases: the evidence a flow's Gemini step judges, and the answer it should give.

A dataset is a JSON file of cases per flow. Media are named by `gs://` URI or by a path relative
to the dataset file; `gs://` media are downloaded once into a local cache. Evidence written by
the run scripts is overwritten by the next run of the same VIN, so a dataset points at a frozen
copy (see scripts/snapshot_eval_media.py), never at the live evidence paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from marshal_ai.records import BuildSheet, Verdict

CACHE = Path(os.environ.get("MARSHAL_EVAL_CACHE", Path.home() / ".cache" / "marshal-eval"))


class GateCase(BaseModel):
    """Flow 3: four gate views of one car and the build sheet it is compared with."""

    id: str
    views: dict[str, str] = Field(description="view name (front, rear, left, right) -> media")
    build_sheet: BuildSheet
    expect_verdicts: list[Verdict] = Field(description="verdicts that count as correct")
    expect_mismatch_items: list[str] = Field(default_factory=list,
                                             description="build sheet items that should be flagged")
    reference: dict[str, str] | None = Field(default=None, description="views of a car that passed this gate "
                                             "in the same light, which findings are checked against")
    note: str = ""


class TrackCase(BaseModel):
    """Flow 4: one routine step's frame, what the car's electronics reported, and the truth."""

    id: str
    step: int
    frame: str
    telemetry: dict
    reference: str | None = Field(default=None, description="a frame from a car that passed this step")
    expect_seen: bool = Field(description="the expected result really is visible in the frame")
    note: str = ""


class ExpectedObject(BaseModel):
    """Something the assistance card should box, and where it really is, labelled by hand."""

    kind: str = Field(description="obstacle, person, vehicle, moving_equipment")
    box_2d: list[int] | None = Field(default=None, description="[ymin, xmin, ymax, xmax], 0-1000 of frame 0")


class AssistCase(BaseModel):
    """Flow 2: the frames from a stopped car, its request, and the answer the desk should give."""

    id: str
    frames: list[str]
    reason: str
    location: str | None = None
    options: list[str]
    expect_people: bool
    expect_recommended: str
    accept_recommended: list[str] = Field(default_factory=list, description="other answers that are also right, "
                                          "e.g. any option when nobody is near")
    expect_objects: list[ExpectedObject] = Field(default_factory=list, description="what the card should box")
    note: str = ""


class DamageCase(BaseModel):
    """Flow 3's defect check on one photo of a car, damaged or not (see marshal_ai.eval.damage)."""

    id: str
    image: str
    expect_damaged: bool
    expect_kinds: list[str] = Field(default_factory=list,
                                    description="damage kinds present, of marshal_ai.eval.damage.KIND_WORDS")
    expect_zones: list[str] = Field(default_factory=list,
                                    description="gate zones the damage is in; empty when none applies")
    reference: str | None = Field(default=None, description="a photo of an undamaged car to check findings against")
    note: str = ""


class ReportCase(BaseModel):
    """Flow 5: a car's gate and track results, and the rework the design's bay table gives them."""

    id: str
    build_sheet: BuildSheet
    visual: dict = Field(description="the gate's InspectionResult, without vin and kind")
    functional: dict = Field(description="the track's InspectionResult, without vin and kind")
    expect_bays: list[str] = Field(description="bays to visit, safety first (mechanical, electrical, body, paint, "
                                               "production_control)")
    note: str = ""


class ScenarioCase(BaseModel):
    """Flow 6: a request (often an assistance case) and what a test faithful to it must contain."""

    id: str
    request: str
    source: str = "supervisor request"
    expect_zone: str
    expect_actors: list[str] = Field(description="actor types the test must have")
    expect_at: dict[str, str] = Field(default_factory=dict,
                                      description="actor type -> the map point it must be at")
    expect_metrics: list[str] = Field(default_factory=list,
                                      description="pass criteria the test must check, by name")
    expect_time_of_day: str | None = None
    note: str = ""


class SearchCase(BaseModel):
    """Flow 7: a passing scenario to search from, against the quick simulator's driving system."""

    id: str
    spec: dict = Field(description="a ScenarioSpec the driving system under test passes")
    budget: int = 60
    random_seeds: int = Field(default=5, description="random-search runs, the baseline Gemini must beat")
    note: str = ""


class Dataset(BaseModel):
    name: str
    description: str = ""
    gate: list[GateCase] = Field(default_factory=list)
    track: list[TrackCase] = Field(default_factory=list)
    assist: list[AssistCase] = Field(default_factory=list)
    damage: list[DamageCase] = Field(default_factory=list)
    report: list[ReportCase] = Field(default_factory=list)
    scenario: list[ScenarioCase] = Field(default_factory=list)
    search: list[SearchCase] = Field(default_factory=list)


def load_dataset(path: str | Path) -> tuple[Dataset, Path]:
    """The dataset, and the directory its relative media paths start from."""
    path = Path(path)
    return Dataset.model_validate(json.loads(path.read_text())), path.parent


class MediaCache:
    """Bytes for a dataset's media, from local paths or `gs://` URIs cached on disk."""

    def __init__(self, base: Path, cache: Path = CACHE, client=None):
        self.base, self.cache, self._client = base, cache, client

    def path(self, ref: str) -> Path:
        if not ref.startswith("gs://"):
            return (self.base / ref).resolve()
        bucket, _, name = ref[len("gs://"):].partition("/")
        local = self.cache / bucket / name
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            if self._client is None:
                from google.cloud import storage
                self._client = storage.Client()
            self._client.bucket(bucket).blob(name).download_to_filename(str(local))
        return local

    def read(self, ref: str) -> bytes:
        return self.path(ref).read_bytes()
