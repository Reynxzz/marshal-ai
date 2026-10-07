import pytest

from marshal_ai.app import MarshalApp, set_app
from marshal_ai.config import Settings
from marshal_ai.demo import seed_demo
from marshal_ai.runtime import build_runtime, set_runtime

OFFLINE = Settings(bus="memory", store="memory", driving="fake", llm="offline")


@pytest.fixture
def app():
    rt = build_runtime(OFFLINE)
    marshal = MarshalApp(rt)
    set_runtime(rt)
    set_app(marshal)
    yield marshal
    close = getattr(rt.driving, "close", None)
    if close is not None:
        close()
    set_app(None)
    set_runtime(None)


@pytest.fixture
def plant(app):
    """The app with demo cars seeded; call plant(vins) to seed only some."""
    def seed(vins=None):
        seed_demo(app.rt, vins)
        return app
    return seed
