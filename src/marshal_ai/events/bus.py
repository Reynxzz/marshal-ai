"""Event bus: in-memory for local dev and tests, Google Cloud Pub/Sub on GCP.

Messages are pydantic models on the way in and JSON dicts on the way out, so a
subscriber never depends on the publisher's Python classes.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Protocol

from pydantic import BaseModel

from marshal_ai.events.topics import Topic

log = logging.getLogger(__name__)

Handler = Callable[[dict], "Awaitable[None] | None"]


class EventBus(Protocol):
    async def publish(self, topic: Topic, message: BaseModel | dict) -> None: ...
    def subscribe(self, topic: Topic, handler: Handler) -> None: ...


def _to_dict(message: BaseModel | dict) -> dict:
    return message.model_dump(mode="json") if isinstance(message, BaseModel) else message


class InMemoryBus:
    def __init__(self) -> None:
        self._handlers: dict[Topic, list[Handler]] = defaultdict(list)
        self.history: dict[Topic, list[dict]] = defaultdict(list)

    async def publish(self, topic: Topic, message: BaseModel | dict) -> None:
        data = _to_dict(message)
        self.history[topic].append(data)
        for handler in self._handlers[topic]:
            try:
                result = handler(data)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("handler failed on %s", topic.value)

    def subscribe(self, topic: Topic, handler: Handler) -> None:
        self._handlers[topic].append(handler)


class PubSubBus:
    """Google Cloud Pub/Sub. Subscriptions are named `<topic>--<subscriber>`."""

    def __init__(self, project: str, subscriber_name: str = "marshal"):
        from google.cloud import pubsub_v1

        self.project = project
        self.subscriber_name = subscriber_name
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._futures: list = []

    def _topic_path(self, topic: Topic) -> str:
        return self._publisher.topic_path(self.project, topic.value)

    async def publish(self, topic: Topic, message: BaseModel | dict) -> None:
        payload = json.dumps(_to_dict(message)).encode()
        future = self._publisher.publish(self._topic_path(topic), payload)
        await asyncio.wrap_future(future)

    def subscribe(self, topic: Topic, handler: Handler) -> None:
        loop = asyncio.get_event_loop()
        sub_path = self._subscriber.subscription_path(
            self.project, f"{topic.value}--{self.subscriber_name}"
        )

        def callback(msg) -> None:
            data = json.loads(msg.data)
            result = handler(data)
            if inspect.isawaitable(result):
                asyncio.run_coroutine_threadsafe(result, loop).result()
            msg.ack()

        self._futures.append(self._subscriber.subscribe(sub_path, callback=callback))


def make_bus(kind: str, project: str | None = None, subscriber_name: str = "marshal") -> EventBus:
    if kind == "memory":
        return InMemoryBus()
    if kind == "gcp":
        if not project:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for the Pub/Sub bus")
        return PubSubBus(project, subscriber_name)
    raise ValueError(f"unknown bus: {kind}")
