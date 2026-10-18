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


# Gate calls that have not answered after this long are sent again, and the first answer is used.
# With thinking capped (llm.GATE_THINKING) a gate call answers in 4-7 s, but on the full v6-gate one
# car took 70 s and another 37 s with no finding and no second check: a single call stalled, not the
# model thinking. Temperature 0 and a fixed seed make the second call the same question, so this only
# changes when the answer arrives; it costs a call only on a stall. At 4 s it would have re-sent most
# calls (81 of 128 defect calls were still out at 4 s) for no gain, so it is set well past a normal one.
HEDGE_AFTER_S = {"visual.": 10.0}


def hedge_after(task: str) -> float | None:
    return next((s for prefix, s in HEDGE_AFTER_S.items() if task.startswith(prefix)), None)


async def ask(rt: "Runtime", task: str, prompt: str, schema: type[T],
              media: list[Media] | None = None, system: str = "") -> T:
    """Call the LLM off the event loop so the plant keeps ticking while Gemini thinks."""
    def call():
        return asyncio.ensure_future(asyncio.to_thread(rt.llm.generate, task, prompt, schema, media, system))

    first = call()
    after = hedge_after(task)
    if after is None:
        return await first
    done, _ = await asyncio.wait({first}, timeout=after)
    if done:
        return first.result()
    log.info("%s: no answer after %.0f s, asking again", task, after)
    second = call()
    done, pending = await asyncio.wait({first, second}, return_when=asyncio.FIRST_COMPLETED)
    winner = done.pop()
    if winner.exception() is not None and pending:      # the other may still answer
        other = pending.pop()
        await asyncio.wait({other})
        return other.result()
    for other in pending:                               # its thread runs on; the answer is not needed
        other.add_done_callback(lambda t: t.cancelled() or t.exception())
    return winner.result()


def use_decisions(rt: "Runtime", task: str) -> bool:
    """Whether to ask `task` as a decision: settings.decisions names it (or "all"), and the model can
    answer it. A test double that answers only the full task is asked in full, as before."""
    settings = getattr(rt, "settings", None)
    wanted = str(getattr(settings, "decisions", "") or "").strip().lower()
    if wanted in ("", "0", "false", "no", "none"):
        return False
    if wanted not in ("1", "all", "true", "yes") and not any(task.startswith(t.strip()) for t in wanted.split(",") if t.strip()):
        return False
    handlers = getattr(rt.llm, "handlers", None)
    return handlers is None or task in handlers


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
