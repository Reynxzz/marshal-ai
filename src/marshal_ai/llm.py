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
import math
from functools import lru_cache
from typing import Any, Callable, ClassVar, Protocol, TypeVar

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


class Decision(BaseModel):
    """A choice from a closed set of options, with a probability for each: Gemini as a decision model.

    Asked for one, Gemini answers with a single option number and nothing else, and the probabilities
    come from the log-probabilities of that one token, not from a number the model writes. One output
    token instead of a JSON object with a sentence in it: on the lamp check 2.0 s against 3.3 s, the
    same verdict. Get a schema for a set of options with `decision_schema("yes", "no")`.
    """

    OPTIONS: ClassVar[tuple[str, ...]] = ()
    choice: str
    probs: dict[str, float]

    @property
    def confidence(self) -> float:
        return self.probs.get(self.choice, 0.0)

    def p(self, option: str) -> float:
        return self.probs.get(option, 0.0)


@lru_cache(maxsize=None)
def decision_schema(*options: str) -> type[Decision]:
    """The Decision type for these options, in this order."""
    if len(options) < 2:
        raise ValueError("a decision needs at least two options")
    return type(f"Decision_{len(options)}", (Decision,), {"OPTIONS": tuple(options)})


def is_decision(schema) -> bool:
    return isinstance(schema, type) and issubclass(schema, Decision)


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
TASK_SINGLE_IMAGE_RESOLUTION = {"track.step": "high", "track.decide": "high"}

# Decisions think not at all. MARSHAL_PARK_THINKING gives parking (drive.park) a budget for
# experiments: in the moves mode, 1024 tokens made it slower (3.5 s a move) and no better.
DECISION_THINKING = {"drive.park": int(__import__("os").getenv("MARSHAL_PARK_THINKING", "0"))}

# Gemini 3 models on Vertex AI are served from the global endpoint only (gemini-3.8-flash: 404 in
# us-central1, 2026-10-17), and they return no log-probabilities (400 "Logprobs is not supported
# for this model"). A decision on such a model is its answer alone, at probability 1, unless
# MARSHAL_DECISION_MODEL sends decisions to a model that has them.
#
# Marshal mixes the two (config.Settings, 2026-10-17). gemini-3.8-flash answers the reasoning tasks -
# the assistance card, reports, scenarios, the test lab - and boxed the object on v7-assist at IoU
# 0.95, first-try scenarios 1.0 (from 0.75). gemini-2.5-flash keeps the gate, the damage check, the
# lamp check and every decision: on 3.8 the gate fell to 0.84 accuracy with 15% false alarms (dents
# seen on clean grey cars) at 13 s median against 5 s, damage found 93% -> 83%, and a decision has
# no probability to hold back a doubtful car with. 3.8 always thinks (budget 0 still thinks ~50
# tokens; thinking_level "minimal" is refused, "low" was slower than the budgets here), about 3 s a
# call at least against 0.8 s for 2.5. Every Gemini 3 model tried refuses logprobs.
def _location(model: str) -> str | None:
    """Vertex AI location for this model: MARSHAL_GEMINI_LOCATION, else global for Gemini 3, else
    GOOGLE_CLOUD_LOCATION (the client's own default)."""
    import os
    return os.getenv("MARSHAL_GEMINI_LOCATION") or ("global" if model.startswith("gemini-3") else None)


def has_logprobs(model: str) -> bool:
    return not model.startswith("gemini-3")


TASK_THINKING_BUDGET = {"assist.locate": 0, "assist.assess": 0, "visual.defects": GATE_THINKING,
                        "visual.variant": GATE_THINKING, "visual.verify": GATE_THINKING}


class GeminiLLM:
    """google-genai client; reads Vertex AI or API key settings from the environment."""

    def __init__(self, model: str, temperature: float = 0.0, seed: int | None = 7,
                 thinking_budget: int | None = None, media_resolution: str | None = None,
                 decision_model: str | None = None, task_models: dict[str, str] | None = None):
        # temperature 0 and a fixed seed: the same plant situation gets (nearly) the same answer,
        # so demos and release decisions are repeatable
        self.model = model
        # the model that answers decisions (llm.Decision); None: the same model
        self.decision_model = decision_model or model
        # task name or prefix ("visual.") -> the model for those tasks; the longest match wins
        self.task_models = dict(task_models or {})
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
        return self.client_for(self.model)

    def model_for(self, task: str) -> str:
        """The model a (non-decision) task goes to: a task_models entry, else the client's model."""
        hits = [k for k in self.task_models if task == k or (k.endswith(".") and task.startswith(k))]
        return self.task_models[max(hits, key=len)] if hits else self.model

    def client_for(self, model: str):
        """One client per thread and location.

        `ask` runs each call with asyncio.to_thread, and flow 3 issues five at once (four views
        plus the variant check). Sharing a single google-genai client across those threads ended
        with "Cannot send a request, as the client has been closed" - its underlying httpx client
        is not safe to share that way. A client per thread costs little and removes the race.
        """
        clients = self._local.__dict__.setdefault("clients", {})
        location = _location(model)
        if location not in clients:
            from google import genai
            clients[location] = genai.Client(**({"location": location} if location else {}))
        return clients[location]

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

        if is_decision(schema):
            return self._decide(task, prompt, schema, media, system)
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
        model = self.model_for(task)
        response = self.client_for(model).models.generate_content(
            model=model,
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

    def _parts(self, prompt, media):
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
                raise LLMError(f"media {m.label!r} has no data or uri")
        parts.append(types.Part.from_text(text=prompt))
        return parts

    def _decide(self, task, prompt, schema, media, system):
        """One option number back, constrained to the options; probabilities from its logprobs.

        With logprobs the question is asked once per rotation of the option order, all at once, and
        the probabilities averaged (the L0 idea in nokia-applied-research/AnyJev): a model leans
        towards whichever option is listed first or numbered 1, and each option taking each place
        cancels that lean. MARSHAL_DECISION_ROTATIONS caps the rotations; off (1) by default. On
        gemini-2.5-flash, 2026-10-17, with up to 4: the gate kept its answers (v6-gate 0.977, no false
        alarm) at 11.6 s median against about 5 s, as each screen became two calls in flight; route
        choice fell from 18 to 16 of 20 ("people where both ways meet" went to p=0.64 and the wrong
        way); the lamp check left fewer cars unsure (13% against 23%) but failed a healthy one once;
        parking was unchanged (8 of 12). Without logprobs each rotation would answer at probability
        1, so it is asked once.
        """
        options = schema.OPTIONS
        model = self.decision_model
        n = len(options)
        rotations = rotation_shifts(n, decision_rotations()) if has_logprobs(model) else [0]
        if len(rotations) == 1:
            return schema(**self._decide_once(task, prompt, options, rotations[0], media, system))
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(rotations)) as pool:
            outs = list(pool.map(lambda k: self._decide_once(task, prompt, options, k, media, system),
                                 rotations))
        return schema(**average_decisions(outs, options))

    def _decide_once(self, task, prompt, options, shift, media, system) -> dict:
        """The decision with the options listed starting from options[shift]."""
        from google.genai import types

        model = self.decision_model
        logprobs = has_logprobs(model)
        order = tuple(options[(shift + i) % len(options)] for i in range(len(options)))
        numbers = [str(i + 1) for i in range(len(order))]
        listed = "\n".join(f"{n} = {o}" for n, o in zip(numbers, order))
        resolution = self.media_resolution if media else None
        if resolution is None and media and len(media) == 1:
            resolution = TASK_SINGLE_IMAGE_RESOLUTION.get(task)
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            seed=self.seed,
            response_mime_type="text/x.enum",
            response_schema={"type": "STRING", "enum": numbers},
            response_logprobs=True if logprobs else None,
            logprobs=min(len(numbers), 20) if logprobs else None,
            # a decision is the answer itself: thinking would cost the time this is meant to save,
            # except for a task that has to plan ahead (DECISION_THINKING)
            thinking_config=types.ThinkingConfig(thinking_budget=DECISION_THINKING.get(task, 0)),
            system_instruction=system or None,
            media_resolution=(getattr(types.MediaResolution, f"MEDIA_RESOLUTION_{resolution.upper()}")
                              if resolution else None),
        )
        response = self.client_for(model).models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=self._parts(
                f"{prompt}\n\nOptions:\n{listed}\nAnswer with the number of one option.", media))],
            config=config,
        )
        return decision_from_response(response, numbers, order)


def decision_rotations() -> int:
    import os
    return max(1, int(os.getenv("MARSHAL_DECISION_ROTATIONS", "1")))


def rotation_shifts(n: int, cap: int) -> list[int]:
    """Which rotations of n options to ask: all of them up to the cap, else spread evenly."""
    k = min(n, cap)
    return sorted({round(i * n / k) % n for i in range(k)})


def average_decisions(outs: list[dict], options: tuple[str, ...]) -> dict:
    """The mean probability of each option over the rotations; the choice is the likeliest."""
    probs = {o: sum(out["probs"].get(o, 0.0) for out in outs) / len(outs) for o in options}
    return {"choice": max(options, key=lambda o: probs[o]), "probs": probs}


def decision_from_response(response, numbers: list[str], options: tuple[str, ...]) -> dict:
    """The chosen option and a probability per option, from the first token's top candidates.

    Options the model gave no probability to get none; the rest are renormalised over the options,
    so probability mass on stray tokens (a bracket, a word) does not count. Without logprobs the
    answer's own text is the choice, at probability 1.
    """
    text = (response.text or "").strip()
    try:
        top = response.candidates[0].logprobs_result.top_candidates[0].candidates
        raw = {c.token.strip(): c.log_probability for c in top}
    except (AttributeError, IndexError, TypeError):
        raw = {}
    weights = [math.exp(raw[n]) if n in raw else 0.0 for n in numbers]
    total = sum(weights)
    if total <= 0:
        if text not in numbers:
            raise LLMError(f"decision answer {text!r} is not one of {numbers}")
        weights, total = [1.0 if n == text else 0.0 for n in numbers], 1.0
    probs = {o: w / total for o, w in zip(options, weights)}
    choice = options[numbers.index(text)] if text in numbers else max(probs, key=probs.get)
    return {"choice": choice, "probs": probs}


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
