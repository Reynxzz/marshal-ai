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


class AssistCase(BaseModel):
    """Flow 2: the frames from a stopped car, its request, and the answer the desk should give."""

    id: str
    frames: list[str]
    reason: str
    location: str | None = None
    options: list[str]
    expect_people: bool
    expect_recommended: str
    note: str = ""


class Dataset(BaseModel):
    name: str
    description: str = ""
    gate: list[GateCase] = Field(default_factory=list)
    track: list[TrackCase] = Field(default_factory=list)
    assist: list[AssistCase] = Field(default_factory=list)


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
