"""Record history, so a verdict can be audited after the live copy has moved on.

The live store keeps one current copy per record. The design wants history in BigQuery as well,
so quality staff can search by VIN, defect type or shift long after a car has shipped.

Deviation worth knowing: `store/__init__.py` says history is written "by the pipelines that
produce them, not through this store". This appends on every `put` instead, by wrapping the
store. Per-pipeline calls only capture the moments someone remembered to add one, and a car's
record is updated many times - released, inspected, reported, reworked, re-inspected - so that
approach silently loses most of the trail. Wrapping the store cannot miss a state change.

One table holds every kind rather than six mirrors of the pydantic models, which would drift
from them. `kind` and `key` cover VIN lookups, `recorded_at` covers shifts, and `payload` is queryable
with BigQuery's JSON functions for anything inside the record:

    SELECT recorded_at, JSON_VALUE(payload, '$.verdict') AS verdict
    FROM marshal.record_history
    WHERE kind = 'CarReport' AND key = 'VIN-DARK' ORDER BY recorded_at

Appends are best-effort, like artifact uploads: losing history must never change a verdict.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel

log = logging.getLogger("marshal.history")

DATASET = "marshal"
TABLE = "record_history"

SCHEMA = [
    # not "at": that is a reserved keyword in BigQuery SQL, so every query would need backticks
    ("recorded_at", "TIMESTAMP", "when the record reached this state"),
    ("kind", "STRING", "record type, e.g. CarReport"),
    ("key", "STRING", "the record's own id: VIN, case id, run id"),
    ("payload", "JSON", "the whole record as stored"),
]


class History(Protocol):
    def append(self, kind: str, key: str, payload: dict) -> bool: ...


class NoHistory:
    """Keeps no history; the live store is the only copy."""

    name = "none"

    def append(self, kind: str, key: str, payload: dict) -> bool:
        return False


class BigQueryHistory:
    name = "bq"

    def __init__(self, project: str, dataset: str = DATASET, table: str = TABLE, client=None,
                 attempts: int = 3, retry_delay_s: float = 2.0):
        self.project, self.dataset, self.table = project, dataset, table
        self.attempts, self.retry_delay_s = attempts, retry_delay_s
        self._client = client
        self._ready = False
        self._lock = threading.Lock()

    @property
    def table_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    def _ensure(self):
        with self._lock:
            if self._client is None:
                from google.cloud import bigquery
                self._client = bigquery.Client(project=self.project)
            if not self._ready:
                self._create_if_missing()
                self._ready = True
            return self._client

    def _create_if_missing(self) -> None:
        from google.cloud import bigquery

        dataset_ref = bigquery.Dataset(f"{self.project}.{self.dataset}")
        dataset_ref.location = "asia-southeast2"
        self._client.create_dataset(dataset_ref, exists_ok=True)
        table = bigquery.Table(self.table_id, schema=[
            bigquery.SchemaField(name, kind, description=doc) for name, kind, doc in SCHEMA
        ])
        # History is read by time and by record; partitioning keeps a shift query cheap.
        table.time_partitioning = bigquery.TimePartitioning(field="recorded_at")
        table.clustering_fields = ["kind", "key"]
        self._client.create_table(table, exists_ok=True)

    def append(self, kind: str, key: str, payload: dict) -> bool:
        row = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "key": key,
            "payload": json.dumps(payload, default=str),
        }
        # A table is not immediately available to streaming inserts after it is created:
        # BigQuery answers 404 for a few seconds. Retry briefly rather than losing the first
        # rows of a run, but never for long, because history must not hold up a verdict.
        last = ""
        for attempt in range(self.attempts):
            try:
                errors = self._ensure().insert_rows_json(self.table_id, [row])
            except Exception as exc:          # history is evidence, not a gate
                last = str(exc)
            else:
                if not errors:
                    return True
                last = str(errors)
            if attempt + 1 < self.attempts:
                time.sleep(self.retry_delay_s * (attempt + 1))
        log.warning("could not append %s %s to history (%s)", kind, key, last[:200])
        return False


class WithHistory:
    """A store that also appends every state change to history."""

    def __init__(self, store, history: History):
        self._store = store
        self.history = history

    def put(self, record: BaseModel) -> None:
        self._store.put(record)
        from marshal_ai.store import record_key
        self.history.append(type(record).__name__, record_key(record),
                            record.model_dump(mode="json"))

    def get(self, model, key):
        return self._store.get(model, key)

    def list(self, model):
        return self._store.list(model)

    def delete(self, model, key) -> None:
        self._store.delete(model, key)


def make_history(kind: str, project: str | None = None, dataset: str = DATASET) -> History:
    if kind != "bq":
        return NoHistory()
    if not project:
        log.warning("MARSHAL_HISTORY=bq needs GOOGLE_CLOUD_PROJECT; keeping no history")
        return NoHistory()
    return BigQueryHistory(project, dataset)
