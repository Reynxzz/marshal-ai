from marshal_ai.driving.api import CommandRejected, DrivingSystem, MissionClient
from marshal_ai.driving.messages import (
    AssistChoose, AssistRequest, MissionDone, MissionGo, MissionHold, MissionResume, Status,
    TrackStep,
)


def make_driving_system(kind: str, plant_map, **kwargs) -> DrivingSystem:
    if kind == "fake":
        from marshal_ai.driving.fake import FakeDrivingSystem
        return FakeDrivingSystem(plant_map, **kwargs)
    if kind == "metadrive":
        from marshal_ai.driving.metadrive_backend import MetaDriveDrivingSystem
        return MetaDriveDrivingSystem(plant_map, **kwargs)
    if kind == "carla":
        from marshal_ai.driving.carla_backend import CarlaDrivingSystem
        return CarlaDrivingSystem(plant_map, **kwargs)
    raise ValueError(f"unknown driving backend: {kind}")


__all__ = [
    "AssistChoose", "AssistRequest", "CommandRejected", "DrivingSystem", "MissionClient",
    "MissionDone", "MissionGo", "MissionHold", "MissionResume", "Status", "TrackStep",
    "make_driving_system",
]
