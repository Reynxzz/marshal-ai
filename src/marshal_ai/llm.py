"""The one place Marshal's flows call Gemini.

Each call names a task (e.g. "assist.assess"), gives a prompt and optional media, and
gets back a typed result. `GeminiLLM` is production. `FakeLLM` answers from handlers,
used by tests and by the offline demo (see marshal_ai.offline). `RoutedLLM` sends some
tasks to one and the rest to the other, e.g. fake perception with real Gemini reasoning.
"""

from __future__ import annotations

import json
import threading
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Media:
    """An image or clip. `truth` is simulation-only ground truth and is never sent to Gemini."""

    mime: str
    uri: str | None = None
    data: bytes | None = None
    label: str = ""
    truth: dict | None = None


@dataclass
class LLMCall:
    task: str
    prompt: str
    media: list[Media]
    system: str


class LLM(Protocol):
    def generate(self, task: str, prompt: str, schema: type[T] | None = None,
                 media: list[Media] | None = None, system: str = "") -> T | dict: ...


class LLMError(RuntimeError):
    pass


# Tasks that answer better without thinking, whatever the client's own budget. Boxing objects is
# one: Google advises thinking off for detection on 2.5 models, and on the scene 3 frame the worker's
# box overlapped the hand-labelled one 0.8 with it off and 0.22 with it on (assist.locate).
#
# The gate's three tasks think at most 512 tokens. Left to itself gemini-2.5-flash sometimes thought
# for 10-20 s on one view, and the gate waits for its slowest call: on 16 v6-gate cases x 2 the gate
# took median 5.6 s, slowest 22.8 s; capped, 5.2 s and 7.9 s, same answers. Damage found on v2-damage
# held (93%, scratches 80%), and v1 stayed all correct. Thinking off failed most clean cars (first-pass
# yield 0.31), and lower image resolution cost small damage (scratches 75% -> 60% at low).
GATE_THINKING = 512
#
# The assistance card's assessment does not think either. It was the card's slow call, 13 s median and
# up to 20 s; without thinking the card took 4.3 s median (19 of 22 under 5 s) on dataset v7-assist
# (11 stops, 8 with a person in view, x 2) with the same answers: right recommendation, wait whenever
# a person was in view, no high-risk option - and the desk's rules are enforced in code whatever the
# model says. Capped at 512 it took 7.0 s. Without thinking it first wrote a thinner situation line
# that left the people out (2 of 16), so the field now asks for them (16 of 16, against 12 before).
# Tasks whose single image needs fine detail: the track's stacked lamp crops (flows/functional.py
# pair_image), where a lit strip and a dark lens are a few pixels apart at the default resolution.
TASK_SINGLE_IMAGE_RESOLUTION = {"track.step": "high"}

TASK_THINKING_BUDGET = {"assist.locate": 0, "assist.assess": 0, "visual.defects": GATE_THINKING,
                        "visual.variant": GATE_THINKING, "visual.verify": GATE_THINKING}


class GeminiLLM:
    """google-genai client; reads Vertex AI or API key settings from the environment."""

    def __init__(self, model: str, temperature: float = 0.0, seed: int | None = 7,
                 thinking_budget: int | None = None, media_resolution: str | None = None):
        # temperature 0 and a fixed seed: the same plant situation gets (nearly) the same answer,
        # so demos and release decisions are repeatable
        self.model = model
        self.temperature = temperature
        self.seed = seed
        # None leaves the model's own default. Flash thinks by default and flash-lite does not;
        # on the gate, flash-lite without thinking flagged a clean car (python -m marshal_ai.eval).
        self.thinking_budget = thinking_budget
        # "low", "medium" or "high": how many tokens Gemini spends reading each image (None: its default)
        self.media_resolution = media_resolution
        self._local = threading.local()

    @property
    def client(self):
        """One client per thread.

        `ask` runs each call with asyncio.to_thread, and flow 3 issues five at once (four views
        plus the variant check). Sharing a single google-genai client across those threads ended
        with "Cannot send a request, as the client has been closed" - its underlying httpx client
        is not safe to share that way. A client per thread costs little and removes the race.
        """
        client = getattr(self._local, "client", None)
        if client is None:
            from google import genai
            client = self._local.client = genai.Client()
        return client

    # No lock: flow 3's five questions (four views and the variant check) run at once, so the gate
    # takes about its slowest call rather than their sum - 12 s instead of 33 s on
    # gemini-2.5-flash, same answers (python -m marshal_ai.eval, dataset v1).
    #
    # Calls used to queue behind a lock, because concurrent calls failed intermittently with
    # pydantic rejecting a JSON schema as a model ("5 validation errors for BaseModel ... Extra
    # inputs are not permitted", naming ViewAssessment in one run and VariantAssessment in
    # another): the SDK turning one call's pydantic class into a schema got mixed up with
    # another's. The schema now goes over as plain JSON (`response_json_schema`) and the answer is
    # validated here, so the SDK never handles the classes. ~200 concurrent gate calls showed no
    # such failure. The per-thread client above still fixes its own, separate fault.

    def generate(self, task, prompt, schema=None, media=None, system=""):
        from google.genai import types

        parts: list = []
        for m in media or []:
            if m.label:
                parts.append(types.Part.from_text(text=f"[{m.label}]"))
            if m.data is not None:
                parts.append(types.Part.from_bytes(data=m.data, mime_type=m.mime))
            elif m.uri is not None:
                parts.append(types.Part.from_uri(file_uri=m.uri, mime_type=m.mime))
            else:
                raise LLMError(f"{task}: media {m.label!r} has no data or uri")
        parts.append(types.Part.from_text(text=prompt))
        budget = TASK_THINKING_BUDGET.get(task, self.thinking_budget)
        resolution = self.media_resolution if media else None
        if resolution is None and media and len(media) == 1:
            # Vertex allows HIGH for a single image only (400 INVALID_ARGUMENT otherwise)
            resolution = TASK_SINGLE_IMAGE_RESOLUTION.get(task)
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            seed=self.seed,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema() if schema is not None else None,
            system_instruction=system or None,
            thinking_config=None if budget is None else types.ThinkingConfig(thinking_budget=budget),
            media_resolution=(getattr(types.MediaResolution, f"MEDIA_RESOLUTION_{resolution.upper()}")
                              if resolution else None),
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
        )
        if schema is not None:
            try:
                return schema.model_validate_json(response.text or "")
            except ValueError as e:
                raise LLMError(f"{task}: response did not match schema: {(response.text or '')[:300]}") from e
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            raise LLMError(f"{task}: response was not JSON: {response.text[:300]}") from e


Handler = Callable[[LLMCall], Any]


@dataclass
class FakeLLM:
    """Answers each task with a handler. Handlers return a model, a dict, or raise."""

    handlers: dict[str, Handler] = field(default_factory=dict)
    calls: list[LLMCall] = field(default_factory=list)

    def on(self, task: str, handler: Handler) -> "FakeLLM":
        self.handlers[task] = handler
        return self

    def generate(self, task, prompt, schema=None, media=None, system=""):
        call = LLMCall(task, prompt, list(media or []), system)
        self.calls.append(call)
        if task not in self.handlers:
            raise LLMError(f"FakeLLM has no handler for task {task!r}")
        out = self.handlers[task](call)
        if schema is not None and not isinstance(out, schema):
            out = schema.model_validate(out)
        return out


@dataclass
class RoutedLLM:
    """Tasks whose name starts with a prefix in `routes` go to that LLM; the rest to `default`."""

    default: LLM
    routes: dict[str, LLM] = field(default_factory=dict)

    def generate(self, task, prompt, schema=None, media=None, system=""):
        for prefix, llm in self.routes.items():
            if task.startswith(prefix):
                return llm.generate(task, prompt, schema, media, system)
        return self.default.generate(task, prompt, schema, media, system)


def to_prompt_json(obj: Any) -> str:
    """Compact JSON for prompts."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, indent=1, default=str)
