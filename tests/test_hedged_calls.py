"""A gate call that stalls is sent again and the first answer is used."""

import threading
import time

from pydantic import BaseModel

from marshal_ai.flows import base
from marshal_ai.flows.base import ask


class Answer(BaseModel):
    n: int


class _LLM:
    def __init__(self, delays, fail_first=False):
        self.delays, self.fail_first, self.calls = list(delays), fail_first, 0
        self.lock = threading.Lock()

    def generate(self, task, prompt, schema=None, media=None, system=""):
        with self.lock:
            i = self.calls
            self.calls += 1
        time.sleep(self.delays[i])
        if self.fail_first and i == 0:
            raise RuntimeError("first call failed")
        return Answer(n=i)


class _RT:
    def __init__(self, llm):
        self.llm = llm


async def test_a_prompt_answer_is_never_asked_twice(monkeypatch):
    monkeypatch.setitem(base.HEDGE_AFTER_S, "visual.", 0.2)
    llm = _LLM([0.01])
    assert (await ask(_RT(llm), "visual.defects", "p", Answer)).n == 0 and llm.calls == 1


async def test_a_stalled_call_is_sent_again_and_the_quicker_answer_used(monkeypatch):
    monkeypatch.setitem(base.HEDGE_AFTER_S, "visual.", 0.1)
    llm = _LLM([1.0, 0.05])
    start = time.perf_counter()
    answer = await ask(_RT(llm), "visual.defects", "p", Answer)
    assert answer.n == 1 and llm.calls == 2 and time.perf_counter() - start < 0.6


async def test_if_the_quicker_call_fails_the_other_still_answers(monkeypatch):
    monkeypatch.setitem(base.HEDGE_AFTER_S, "visual.", 0.05)
    llm = _LLM([0.2, 0.4], fail_first=True)
    assert (await ask(_RT(llm), "visual.defects", "p", Answer)).n == 1


async def test_only_the_gate_is_sent_again():
    assert base.hedge_after("visual.verify") == 10.0
    assert base.hedge_after("report.write") is None and base.hedge_after("assist.assess") is None
