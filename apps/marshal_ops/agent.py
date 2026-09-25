"""Entry point for `adk web apps` / `adk run apps/marshal_ops`."""

from marshal_ai.agents.coordinator import root_agent
from marshal_ai.app import get_app
from marshal_ai.demo import seed_demo

seed_demo(get_app().rt)

__all__ = ["root_agent"]
