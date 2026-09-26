"""History must capture every state change and never block a verdict."""

from __future__ import annotations

import json

import pytest

from marshal_ai.history import (BigQueryHistory, NoHistory, WithHistory, make_history)
from marshal_ai.records import BuildSheet, Car, CarState, InspectionResult, Verdict
from marshal_ai.store import InMemoryStore


class _Client:
    def __init__(self, errors=None, explode=False):
        self.rows: list[tuple[str, list[dict]]] = []
        self._errors = errors or []
        self._explode = explode
        self.created = []

    def create_dataset(self, dataset, exists_ok=False):
        self.created.append(("dataset", getattr(dataset, "dataset_id", str(dataset))))

    def create_table(self, table, exists_ok=False):
        self.created.append(("table", str(table)))

    def insert_rows_json(self, table_id, rows):
        if self._explode:
            raise RuntimeError("bigquery unavailable")
        self.rows.append((table_id, rows))
        return self._errors


def _history(**kw):
    # retry_delay_s=0 keeps the failure-path tests instant
    return BigQueryHistory("proj", client=_Client(**kw), retry_delay_s=0.0)


def test_the_default_keeps_no_history():
    assert NoHistory().append("Car", "VIN-1", {"vin": "VIN-1"}) is False


def test_a_row_carries_the_kind_key_and_payload():
    history = _history()

    assert history.append("CarReport", "VIN-1", {"vin": "VIN-1", "verdict": "FAIL"}) is True

    [(table_id, [row])] = history._client.rows
    assert table_id == "proj.marshal.record_history"
    assert row["kind"] == "CarReport" and row["key"] == "VIN-1"
    assert json.loads(row["payload"])["verdict"] == "FAIL"
    assert row["recorded_at"].endswith("+00:00") or row["recorded_at"].endswith("Z")


def test_a_transient_failure_is_retried():
    """A freshly created table answers 404 to streaming inserts for a few seconds."""
    class _FlakyClient(_Client):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def insert_rows_json(self, table_id, rows):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("404 Table not found")
            return super().insert_rows_json(table_id, rows)

    history = BigQueryHistory("proj", client=_FlakyClient(), retry_delay_s=0.0)

    assert history.append("Car", "VIN-1", {}) is True
    assert history._client.calls == 2


def test_the_dataset_and_table_are_created_once():
    history = _history()

    history.append("Car", "VIN-1", {})
    history.append("Car", "VIN-2", {})

    assert [kind for kind, _name in history._client.created] == ["dataset", "table"]


def test_an_outage_is_swallowed():
    history = _history(explode=True)

    assert history.append("Car", "VIN-1", {}) is False, "history must never gate a verdict"


def test_rejected_rows_are_reported_as_failure_not_raised():
    history = _history(errors=[{"index": 0, "errors": [{"reason": "invalid"}]}])

    assert history.append("Car", "VIN-1", {}) is False


def test_a_payload_with_awkward_types_still_serialises():
    from datetime import datetime

    history = _history()

    assert history.append("Car", "VIN-1", {"at": datetime(2026, 1, 1)}) is True


@pytest.mark.parametrize("kind", ["none", "", "memory"])
def test_only_bq_keeps_history(kind):
    assert isinstance(make_history(kind, project="p"), NoHistory)


def test_bq_without_a_project_degrades_instead_of_failing():
    assert isinstance(make_history("bq"), NoHistory)


class _Recorder:
    def __init__(self):
        self.appended: list[tuple[str, str]] = []

    def append(self, kind, key, payload):
        self.appended.append((kind, key))
        return True


def test_the_wrapper_records_every_state_change():
    """A car is put many times - released, inspected, reported - and each must be kept."""
    recorder = _Recorder()
    store = WithHistory(InMemoryStore(), recorder)
    car = Car(vin="VIN-1", build_sheet=BuildSheet(model="sedan", colour="black"))

    store.put(car)
    store.put(car.model_copy(update={"state": CarState.INSPECTION_GATE}))
    store.put(car.model_copy(update={"state": CarState.REPORT}))

    assert recorder.appended == [("Car", "VIN-1")] * 3
    assert store.get(Car, "VIN-1").state is CarState.REPORT, "the live copy still works"


def test_inspection_results_are_keyed_like_the_live_copy():
    recorder = _Recorder()
    store = WithHistory(InMemoryStore(), recorder)

    store.put(InspectionResult(vin="VIN-1", kind="functional", verdict=Verdict.FAIL))

    assert recorder.appended == [("InspectionResult", "VIN-1:functional")]


def test_reads_and_deletes_pass_through_without_history():
    recorder = _Recorder()
    store = WithHistory(InMemoryStore(), recorder)
    car = Car(vin="VIN-1", build_sheet=BuildSheet(model="sedan", colour="black"))
    store.put(car)
    recorder.appended.clear()

    assert store.list(Car)[0].vin == "VIN-1"
    store.delete(Car, "VIN-1")

    assert store.get(Car, "VIN-1") is None
    assert recorder.appended == [], "only state changes are history"
