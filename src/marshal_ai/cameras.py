"""Camera protocols: the gate (flow 3) and the test track (flow 4).

Implementations: fake cameras over FakeWorld (marshal_ai.fakeworld), a directory of
real images (e.g. the public car-damage set used for scratches and dents in the demo),
and CARLA RGB sensors on the VM (marshal_ai.driving.carla_backend).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from marshal_ai.fakeworld import GATE_VIEWS
from marshal_ai.llm import Media


class GateCamera(Protocol):
    def capture(self, vin: str, views: list[str] | None = None) -> list[Media]: ...


class TrackCamera(Protocol):
    def clip(self, vin: str, step: int, name: str) -> Media: ...


class DirectoryGateCamera:
    """Reads <root>/<vin>/<view>.jpg for views front, rear, left, right."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def capture(self, vin: str, views: list[str] | None = None) -> list[Media]:
        out = []
        for view in GATE_VIEWS:
            if views is not None and view not in views:
                continue
            path = self.root / vin / f"{view}.jpg"
            if path.exists():
                out.append(Media(mime="image/jpeg", data=path.read_bytes(),
                                 uri=str(path), label=f"{view} camera"))
        return out
