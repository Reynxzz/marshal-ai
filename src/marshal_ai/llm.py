"""The one place Marshal's flows call Gemini.

Each call names a task (e.g. "assist.assess"), gives a prompt and optional media, and
gets back a typed result. `GeminiLLM` is production. `FakeLLM` answers from handlers,
used by tests and by the offline demo (see marshal_ai.offline). `RoutedLLM` sends some
tasks to one and the rest to the other, e.g. fake perception with real Gemini reasoning.
"""

from __future__ import annotations

import json
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


class GeminiLLM:
    """google-genai client; reads Vertex AI or API key settings from the environment."""

    def __init__(self, model: str, temperature: float = 0.2):
        self.model = model
        self.temperature = temperature
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client()
        return self._client

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
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            response_mime_type="application/json",
            response_schema=schema,
            system_instruction=system or None,
        )
        response = self.client.models.generate_content(
            model=self.model, contents=[types.Content(role="user", parts=parts)], config=config,
        )
        if schema is not None:
            if response.parsed is None:
                raise LLMError(f"{task}: response did not match schema: {response.text[:300]}")
            return response.parsed
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
