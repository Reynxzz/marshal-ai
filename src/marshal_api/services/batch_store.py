"""Batch persistence: Firestore in production, an in-process store for tests and local dev.

The batch runner lives on the CARLA VM, whose root volume is deleted with the instance, so batch
state cannot live on that disk. Firestore already holds the live plant records and is reachable
from the AWS host through Workload Identity Federation, so a batch is one document per run with
its six cars embedded and its events in a subcollection. ``MemoryBatchStore`` mirrors the same
semantics for tests and local runs: one active batch, gap-free event `seq`, tenant scoping.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Protocol

LOCK_COLLECTION, LOCK_DOC, LOCK_FIELD = "batch_locks", "active_carla", "batch_id"
BATCHES = "batches"
EVENTS = "events"
ACTIVE_RESOURCE = "carla"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchStore(Protocol):
    async def create(self, batch: dict, cars: list[dict]) -> bool: ...
    async def get(self, tenant_id: str, batch_id: str) -> dict | None: ...
    async def update(self, tenant_id: str, batch_id: str, fields: dict) -> None: ...
    async def list(self, tenant_id: str, status: str | None, page: int, limit: int) -> tuple[list[dict], int]: ...
    async def append_events(self, tenant_id: str, batch_id: str, events: list[dict]) -> None: ...
    async def get_events(self, tenant_id: str, batch_id: str, since: int, limit: int,
                         flow: int | None) -> tuple[list[dict], bool]: ...
    async def release(self, batch_id: str) -> None: ...
    async def list_active(self) -> list[dict]: ...


class MemoryBatchStore:
    def __init__(self) -> None:
        self._batches: dict[str, dict] = {}
        self._events: dict[str, list[dict]] = {}
        self._active: str | None = None

    async def create(self, batch: dict, cars: list[dict]) -> bool:
        if self._active is not None:
            return False
        doc = copy.deepcopy(batch)
        doc["cars"] = copy.deepcopy(cars)
        doc["next_seq"] = 0
        doc["active_resource"] = ACTIVE_RESOURCE
        self._batches[doc["batch_id"]] = doc
        self._events[doc["batch_id"]] = []
        self._active = doc["batch_id"]
        return True

    def _owned(self, tenant_id: str, batch_id: str) -> dict | None:
        doc = self._batches.get(batch_id)
        return doc if doc is not None and doc["tenant_id"] == tenant_id else None

    async def get(self, tenant_id: str, batch_id: str) -> dict | None:
        doc = self._owned(tenant_id, batch_id)
        return copy.deepcopy(doc) if doc is not None else None

    async def update(self, tenant_id: str, batch_id: str, fields: dict) -> None:
        doc = self._owned(tenant_id, batch_id)
        if doc is None:
            return
        for key, value in fields.items():
            doc[key] = copy.deepcopy(value)

    async def list(self, tenant_id: str, status: str | None, page: int, limit: int) -> tuple[list[dict], int]:
        rows = [doc for doc in self._batches.values() if doc["tenant_id"] == tenant_id]
        if status:
            rows = [doc for doc in rows if doc["status"] == status]
        rows.sort(key=lambda doc: str(doc.get("created_at", "")), reverse=True)
        total = len(rows)
        start = (page - 1) * limit
        return copy.deepcopy(rows[start:start + limit]), total

    async def append_events(self, tenant_id: str, batch_id: str, events: list[dict]) -> None:
        doc = self._owned(tenant_id, batch_id)
        if doc is None:
            return
        seq = int(doc.get("next_seq") or 0)
        for event in events:
            seq += 1
            self._events[batch_id].append({**copy.deepcopy(event), "seq": seq})
        doc["next_seq"] = seq

    async def get_events(self, tenant_id: str, batch_id: str, since: int, limit: int,
                         flow: int | None) -> tuple[list[dict], bool]:
        if self._owned(tenant_id, batch_id) is None:
            return [], False
        rows = [event for event in self._events.get(batch_id, [])
                if event["seq"] > since and (flow is None or event.get("flow") == flow)]
        return copy.deepcopy(rows[:limit]), len(rows) > limit

    async def release(self, batch_id: str) -> None:
        if self._active == batch_id:
            self._active = None
        doc = self._batches.get(batch_id)
        if doc is not None:
            doc["active_resource"] = None

    async def list_active(self) -> list[dict]:
        return [copy.deepcopy(doc) for doc in self._batches.values()
                if doc.get("active_resource") == ACTIVE_RESOURCE]


class FirestoreBatchStore:
    """One document per batch; events in `batches/{id}/events` keyed by `seq`.

    shortcut: `list` scans the tenant's batch documents instead of a composite-index query;
    batches are few per demo day, move the filter server-side if that stops being true.
    """

    def __init__(self, project: str, client: Any | None = None) -> None:
        self._project = project
        self._client = client

    def _db(self):
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client(project=self._project)
        return self._client

    def _batch_ref(self, batch_id: str):
        return self._db().collection(BATCHES).document(batch_id)

    async def create(self, batch: dict, cars: list[dict]) -> bool:
        from google.cloud import firestore

        db = self._db()
        lock_ref = db.collection(LOCK_COLLECTION).document(LOCK_DOC)
        batch_ref = self._batch_ref(batch["batch_id"])

        def _claim() -> bool:
            @firestore.transactional
            def run(transaction) -> bool:
                snapshot = lock_ref.get(transaction=transaction)
                if snapshot.exists and snapshot.get(LOCK_FIELD):
                    return False
                transaction.set(lock_ref, {LOCK_FIELD: batch["batch_id"], "updated_at": utc_now_iso()})
                transaction.set(batch_ref, {**batch, "cars": cars, "next_seq": 0,
                                            "active_resource": ACTIVE_RESOURCE})
                return True

            return run(db.transaction())

        return await asyncio.to_thread(_claim)

    async def get(self, tenant_id: str, batch_id: str) -> dict | None:
        def _read():
            snapshot = self._batch_ref(batch_id).get()
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            return data if data.get("tenant_id") == tenant_id else None

        return await asyncio.to_thread(_read)

    async def update(self, tenant_id: str, batch_id: str, fields: dict) -> None:
        def _write():
            ref = self._batch_ref(batch_id)
            snapshot = ref.get()
            if snapshot.exists and snapshot.to_dict().get("tenant_id") == tenant_id:
                ref.update(fields)

        await asyncio.to_thread(_write)

    async def list(self, tenant_id: str, status: str | None, page: int, limit: int) -> tuple[list[dict], int]:
        def _read():
            query = self._db().collection(BATCHES).where("tenant_id", "==", tenant_id)
            rows = [snapshot.to_dict() for snapshot in query.stream()]
            rows.sort(key=lambda doc: str(doc.get("created_at", "")), reverse=True)
            if status:
                rows = [doc for doc in rows if doc["status"] == status]
            total = len(rows)
            start = (page - 1) * limit
            return rows[start:start + limit], total

        return await asyncio.to_thread(_read)

    async def append_events(self, tenant_id: str, batch_id: str, events: list[dict]) -> None:
        from google.cloud import firestore

        db = self._db()
        batch_ref = self._batch_ref(batch_id)
        events_ref = batch_ref.collection(EVENTS)

        def _append():
            @firestore.transactional
            def run(transaction) -> None:
                snapshot = batch_ref.get(transaction=transaction)
                if not snapshot.exists or snapshot.to_dict().get("tenant_id") != tenant_id:
                    return
                seq = int(snapshot.to_dict().get("next_seq") or 0)
                for event in events:
                    seq += 1
                    transaction.set(events_ref.document(str(seq)), {**event, "seq": seq})
                transaction.update(batch_ref, {"next_seq": seq})

            run(db.transaction())

        await asyncio.to_thread(_append)

    async def get_events(self, tenant_id: str, batch_id: str, since: int, limit: int,
                         flow: int | None) -> tuple[list[dict], bool]:
        def _read():
            batch_ref = self._batch_ref(batch_id)
            snapshot = batch_ref.get()
            if not snapshot.exists or snapshot.to_dict().get("tenant_id") != tenant_id:
                return [], False
            query = batch_ref.collection(EVENTS).where("seq", ">", since)
            if flow is not None:
                query = query.where("flow", "==", flow)
            rows = [doc.to_dict() for doc in query.order_by("seq").limit(limit + 1).stream()]
            return rows[:limit], len(rows) > limit

        return await asyncio.to_thread(_read)

    async def release(self, batch_id: str) -> None:
        def _release():
            lock_ref = self._db().collection(LOCK_COLLECTION).document(LOCK_DOC)
            snapshot = lock_ref.get()
            if snapshot.exists and snapshot.get(LOCK_FIELD) == batch_id:
                lock_ref.update({LOCK_FIELD: None, "updated_at": utc_now_iso()})
            self._batch_ref(batch_id).update({"active_resource": None})

        await asyncio.to_thread(_release)

    async def list_active(self) -> list[dict]:
        def _read():
            query = self._db().collection(BATCHES).where("active_resource", "==", ACTIVE_RESOURCE)
            return [snapshot.to_dict() for snapshot in query.stream()]

        return await asyncio.to_thread(_read)


_STORE: BatchStore | None = None


def build_batch_store(settings) -> BatchStore:
    if settings.batch_store == "firestore":
        if not settings.gcp_project:
            raise ValueError("MARSHAL_BATCH_STORE=firestore needs GOOGLE_CLOUD_PROJECT")
        return FirestoreBatchStore(settings.gcp_project)
    return MemoryBatchStore()


def get_batch_store(settings) -> BatchStore:
    """One process-wide store: the lifespan cleanup and request handlers must see the same one."""
    global _STORE
    if _STORE is None:
        _STORE = build_batch_store(settings)
    return _STORE


def reset_batch_store() -> None:
    global _STORE
    _STORE = None
