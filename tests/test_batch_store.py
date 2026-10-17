from __future__ import annotations

from marshal_api.services.batch_store import MemoryBatchStore


def _batch(batch_id: str, tenant: str = "t1", created_at: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {"batch_id": batch_id, "tenant_id": tenant, "status": "queued",
            "created_at": created_at, "active_resource": "carla"}


async def test_one_active_batch_and_gap_free_events() -> None:
    store = MemoryBatchStore()

    assert await store.create(_batch("BAT-1"), [{"vin": "VIN-1"}])
    assert not await store.create(_batch("BAT-2"), [])

    await store.append_events("t1", "BAT-1", [{"title": "a"}, {"title": "b"}])
    await store.append_events("t1", "BAT-1", [{"title": "c"}])
    rows, more = await store.get_events("t1", "BAT-1", 0, 10, None)
    assert [row["seq"] for row in rows] == [1, 2, 3] and not more
    assert [row["title"] for row in rows] == ["a", "b", "c"]

    assert await store.get_events("t2", "BAT-1", 0, 10, None) == ([], False)

    await store.release("BAT-1")
    assert await store.list_active() == []
    assert await store.create(_batch("BAT-2"), [])
    assert [doc["batch_id"] for doc in await store.list_active()] == ["BAT-2"]


async def test_list_filters_status_and_paginates_newest_first() -> None:
    store = MemoryBatchStore()
    await store.create(_batch("BAT-1", created_at="2026-01-01T00:00:00+00:00"), [])
    await store.release("BAT-1")
    await store.create(_batch("BAT-2", created_at="2026-01-02T00:00:00+00:00"), [])
    await store.update("t1", "BAT-2", {"status": "running"})

    everything, total = await store.list("t1", None, 1, 10)
    assert total == 2 and [doc["batch_id"] for doc in everything] == ["BAT-2", "BAT-1"]

    running, total = await store.list("t1", "running", 1, 10)
    assert total == 1 and running[0]["batch_id"] == "BAT-2"

    other_tenant, total = await store.list("t2", None, 1, 10)
    assert total == 0 and other_tenant == []
