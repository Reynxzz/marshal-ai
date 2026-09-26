"""Helpers shared by the flows."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from marshal_ai.llm import Media
from marshal_ai.records import Car, CarState

if TYPE_CHECKING:
    from marshal_ai.runtime import Runtime

log = logging.getLogger("marshal")

T = TypeVar("T", bound=BaseModel)


async def ask(rt: "Runtime", task: str, prompt: str, schema: type[T],
              media: list[Media] | None = None, system: str = "") -> T:
    """Call the LLM off the event loop so the plant keeps ticking while Gemini thinks."""
    return await asyncio.to_thread(rt.llm.generate, task, prompt, schema, media, system)


async def ask_or(rt: "Runtime", task: str, prompt: str, schema: type[T], fallback: T,
                 media: list[Media] | None = None, system: str = "") -> tuple[T, bool]:
    """Like ask(), but a failed call (network, quota, bad output) returns the flow's safe fallback.

    Every fallback leaves the decision to a person; returns (answer, answered_by_model).
    """
    try:
        return await ask(rt, task, prompt, schema, media, system), True
    except Exception as e:             # the plant must keep running when the model is unreachable
        log.warning("%s: model unavailable, using the safe fallback (%s: %s)", task, type(e).__name__, str(e)[:200])
        return fallback, False


def update_car(rt: "Runtime", vin: str, **changes) -> Car:
    car = rt.store.get(Car, vin)
    if car is None:
        raise KeyError(f"unknown VIN {vin}")
    changes.setdefault("updated_at", rt.clock())
    car = car.model_copy(update=changes)
    rt.store.put(car)
    return car


def cars_in(rt: "Runtime", *states: CarState) -> list[Car]:
    return [c for c in rt.store.list(Car) if c.state in states]
