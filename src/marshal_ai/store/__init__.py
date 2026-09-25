"""Live copies of records: in memory locally, Firestore on GCP.

History (BigQuery) and media (Cloud Storage) are written by the pipelines that
produce them, not through this store.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from marshal_ai.records import AssistanceCase, Car, CarReport, InspectionResult, Scenario, TestRun

M = TypeVar("M", bound=BaseModel)

# collection name and key field per live record
COLLECTIONS: dict[type[BaseModel], tuple[str, str]] = {
    Car: ("cars", "vin"),
    AssistanceCase: ("assistance_cases", "case_id"),
    InspectionResult: ("inspection_results", "vin"),
    CarReport: ("car_reports", "vin"),
    Scenario: ("scenarios", "id"),
    TestRun: ("test_runs", "run_id"),
}


def _key(record: BaseModel) -> tuple[str, str]:
    collection, field = COLLECTIONS[type(record)]
    key = getattr(record, field)
    if isinstance(record, InspectionResult):
        key = f"{key}:{record.kind}"
    return collection, key


class Store(Protocol):
    def put(self, record: BaseModel) -> None: ...
    def get(self, model: type[M], key: str) -> M | None: ...
    def list(self, model: type[M]) -> list[M]: ...
    def delete(self, model: type[BaseModel], key: str) -> None: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}

    def put(self, record: BaseModel) -> None:
        collection, key = _key(record)
        self._data.setdefault(collection, {})[key] = record.model_dump(mode="json")

    def get(self, model: type[M], key: str) -> M | None:
        raw = self._data.get(COLLECTIONS[model][0], {}).get(key)
        return model.model_validate(raw) if raw is not None else None

    def list(self, model: type[M]) -> list[M]:
        return [model.model_validate(r) for r in self._data.get(COLLECTIONS[model][0], {}).values()]

    def delete(self, model: type[BaseModel], key: str) -> None:
        self._data.get(COLLECTIONS[model][0], {}).pop(key, None)


class FirestoreStore:
    def __init__(self, project: str):
        from google.cloud import firestore

        self._db = firestore.Client(project=project)

    def put(self, record: BaseModel) -> None:
        collection, key = _key(record)
        self._db.collection(collection).document(key).set(record.model_dump(mode="json"))

    def get(self, model: type[M], key: str) -> M | None:
        snap = self._db.collection(COLLECTIONS[model][0]).document(key).get()
        return model.model_validate(snap.to_dict()) if snap.exists else None

    def list(self, model: type[M]) -> list[M]:
        return [model.model_validate(d.to_dict())
                for d in self._db.collection(COLLECTIONS[model][0]).stream()]

    def delete(self, model: type[BaseModel], key: str) -> None:
        self._db.collection(COLLECTIONS[model][0]).document(key).delete()


def make_store(kind: str, project: str | None = None) -> Store:
    if kind == "memory":
        return InMemoryStore()
    if kind == "gcp":
        if not project:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for the Firestore store")
        return FirestoreStore(project)
    raise ValueError(f"unknown store: {kind}")
