import pytest

from marshal_ai.carla_connection import (
    CarlaConnectionError,
    CarlaVersionMismatch,
    connect_carla,
)


class _Map:
    name = "Town05"


class _World:
    def get_map(self):
        return _Map()

    def get_actors(self):
        return [object(), object()]


class _Client:
    def __init__(self, host, port, server_version="0.9.16-12-gabcdef"):
        self.host = host
        self.port = port
        self.server_version = server_version
        self.timeout = None

    def set_timeout(self, timeout):
        self.timeout = timeout

    def get_client_version(self):
        return "0.9.16"

    def get_server_version(self):
        return self.server_version

    def get_world(self):
        return _World()


def test_connect_carla_reports_endpoint_version_and_world():
    made = []

    def factory(host, port):
        made.append(_Client(host, port))
        return made[0]

    client, info = connect_carla("127.0.0.1", 2000, 7.5, "0.9.16", factory)

    assert client.timeout == 7.5
    assert info.host == "127.0.0.1" and info.port == 2000
    assert info.client_version == "0.9.16"
    assert info.server_version.startswith("0.9.16")
    assert info.map_name == "Town05" and info.actor_count == 2


def test_connect_carla_rejects_wrong_server_release():
    with pytest.raises(CarlaVersionMismatch, match="server version.*0.9.15"):
        connect_carla(
            expected_version="0.9.16",
            client_factory=lambda host, port: _Client(host, port, "0.9.15"),
        )


def test_connect_carla_wraps_transport_errors_with_endpoint():
    def unavailable(host, port):
        raise TimeoutError("timed out")

    with pytest.raises(CarlaConnectionError, match="localhost:2000.*timed out"):
        connect_carla(client_factory=unavailable)
