"""Non-mutating CARLA endpoint connection and compatibility checks."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class CarlaConnectionError(RuntimeError):
    """The CARLA endpoint could not be queried."""


class CarlaVersionMismatch(CarlaConnectionError):
    """The client and server releases are incompatible."""


@dataclass(frozen=True)
class CarlaServerInfo:
    host: str
    port: int
    client_version: str
    server_version: str
    map_name: str
    actor_count: int


def _release(version: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(version)
    return tuple(map(int, match.groups())) if match else None


def _require_release(actual: str, expected: str, label: str) -> None:
    if _release(actual) != _release(expected):
        raise CarlaVersionMismatch(
            f"CARLA {label} version {actual!r} does not match expected release {expected!r}"
        )


def connect_carla(
    host: str = "localhost",
    port: int = 2000,
    timeout_s: float = 20.0,
    expected_version: str | None = "0.9.16",
    client_factory: Callable[[str, int], Any] | None = None,
) -> tuple[Any, CarlaServerInfo]:
    """Connect and inspect the current world without changing simulator state."""
    if client_factory is None:
        try:
            carla = importlib.import_module("carla")
        except ImportError as exc:
            raise CarlaConnectionError(
                "CARLA client is not installed. Use the 0.9.16 wheel bundled with the "
                "server package on Linux, or install marshal-ai[carla] in a supported environment."
            ) from exc
        client_factory = carla.Client

    try:
        client = client_factory(host, port)
        client.set_timeout(timeout_s)
        client_version = str(client.get_client_version())
        server_version = str(client.get_server_version())
        if expected_version is not None:
            _require_release(server_version, expected_version, "server")
            _require_release(client_version, expected_version, "client")
        elif _release(client_version) != _release(server_version):
            raise CarlaVersionMismatch(
                f"CARLA client {client_version!r} and server {server_version!r} do not match"
            )
        world = client.get_world()
        info = CarlaServerInfo(
            host=host,
            port=port,
            client_version=client_version,
            server_version=server_version,
            map_name=world.get_map().name,
            actor_count=len(world.get_actors()),
        )
        return client, info
    except CarlaConnectionError:
        raise
    except Exception as exc:
        raise CarlaConnectionError(
            f"Could not query CARLA at {host}:{port} within {timeout_s:g}s: {exc}"
        ) from exc
