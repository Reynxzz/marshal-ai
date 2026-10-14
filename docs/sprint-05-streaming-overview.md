# Sprint 5 — Realtime Web Streaming, Overview & Live Integration (`SSE`, `CameraProxy`, `Overview`)

> **Target Phase**: Final Integration & Live End-to-End Operation  
> **Target Files**:
> - `src/marshal_api/streaming/sse.py` (Server-Sent Events broadcaster `/events`)
> - `src/marshal_api/streaming/camera_proxy.py` (Live MJPEG camera proxy `/camera.mjpg`)
> - `src/marshal_api/api/v1/overview.py` (Real DB aggregations for `/overview` and `/kpis`)
> - `src/marshal_api/api/v1/health.py` (Live DB and dependency readiness probes)
> - `src/marshal_api/api/v1/router.py` (Mounting streaming endpoints)
> - `tests/test_streaming.py` & `tests/test_overview.py` (Verification test suites)

---

## 1. Overview & Scope

Sprint 5 completes the full backend service layer and connects `marshal-api` directly to `marshal-fe` in live production mode (`VITE_MOCK_MODE=false`):
1. **Real-time Event Streaming (`GET /events`)**: Provides a Server-Sent Events (SSE) feed of factory activities across Flows 1–7. Supports automatic browser reconnections via a monotonic sequence cursor (`?since=seq`).
2. **CCTV / CARLA Viewer Proxy (`GET /camera.mjpg`)**: Streams live multipart MJPEG video from the CARLA overhead camera or plant gate CCTV. If CARLA is offline, smoothly generates simulated standby frames with timestamps to prevent frontend UI errors.
3. **Control Tower Overview & KPIs (`GET /overview`, `GET /kpis`)**: Replaces placeholder counters with live database aggregations across car states, open assistance cases, pending proposals, and factory throughput.
4. **Health & Readiness (`GET /healthz`, `GET /readyz`)**: Live probes for DB latency, EventBus status, and CARLA simulation port readiness.

---

## 2. Endpoints Covered (8 Endpoints)

| Method | Path | Format | Role Guard | Summary & Functionality |
|:---|:---|:---|:---|:---|
| `GET` | `/events` | SSE | Public / Any Auth | Server-Sent Events stream emitting `FlowEvent` objects. Supports `?since=seq`. |
| `GET` | `/camera.mjpg` | MJPEG | Public / Any Auth | Live multipart MJPEG video stream proxying CARLA viewer or plant CCTV. |
| `GET` | `/overview` | JSON | Any Auth | Aggregates active car state tallies, open assistance, pending proposals, and alerts. |
| `GET` | `/kpis` | JSON | Any Auth | Factory operational metrics (First-Pass Yield, cycle times, hourly throughput). |
| `GET` | `/healthz` | JSON | Public | Liveness probe verifying API server process and database ping. |
| `GET` | `/readyz` | JSON | Public | Readiness probe checking database, EventBus, and CARLA RPC connection. |
| `GET` | `/me` | JSON | Any Auth | Returns current actor profile and permissions based on headers or auth token. |
| `POST`| `/dev/actor` | JSON | Dev / Admin | Switches active mock actor context for local development convenience. |

---

## 3. Server-Sent Events Broadcaster (`streaming/sse.py`)

Subscribes to incoming `EventBus` topics and historical records in the `event_logs` table:

```python
"""Server-Sent Events broadcaster streaming real-time factory flow events."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse
from sqlmodel import select

from marshal_api.db.session import async_session_maker
from marshal_api.models.ops import EventLog
from marshal_api.bridge import get_event_bus
from marshal_api.schemas.common import FlowEvent

router = APIRouter(tags=["streaming"])


async def event_generator(request: Request, since: int = 0) -> AsyncGenerator[dict, None]:
    last_seq = since
    bus = get_event_bus()

    # 1. Backfill any events recorded since the requested cursor
    async with async_session_maker() as session:
        result = await session.execute(
            select(EventLog).where(EventLog.seq > last_seq).order_by(EventLog.seq.asc()).limit(100)
        )
        historical_events = result.scalars().all()
        for ev in historical_events:
            yield {
                "event": "message",
                "id": str(ev.seq),
                "data": FlowEvent.model_validate(ev).model_dump_json(),
            }
            last_seq = ev.seq

    # 2. Stream live events from in-memory queue or event bus
    queue: asyncio.Queue[FlowEvent] = asyncio.Queue()

    # Callback registered to bridge EventBus
    def on_bus_event(msg: dict):
        flow_event = FlowEvent(
            seq=last_seq + 1,
            timestamp=msg.get("timestamp"),
            flow_number=msg.get("flow", 1),
            source=msg.get("source", "system"),
            title=msg.get("title", ""),
            detail=msg.get("detail", ""),
            level=msg.get("level", "info"),
        )
        queue.put_nowait(flow_event)

    # Yield as events arrive
    while True:
        if await request.is_disconnected():
            break

        try:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            last_seq = event.seq
            yield {
                "event": "message",
                "id": str(event.seq),
                "data": event.model_dump_json(),
            }
        except asyncio.TimeoutError:
            # Send keep-alive comment
            yield {"event": "ping", "data": ""}


@router.get("/events")
async def stream_events(request: Request, since: int = Query(default=0, ge=0)):
    return EventSourceResponse(event_generator(request, since))
```

---

## 4. Live Camera Stream Proxy (`streaming/camera_proxy.py`)

Proxies CARLA's headless video feed or renders dynamic standby frames:

```python
"""Multipart MJPEG streaming proxy for CARLA viewer and gate CCTV."""

from __future__ import annotations

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["streaming"])

STANDBY_FRAME = b"..." # Standby JPEG buffer with timestamp placeholder


@router.get("/camera.mjpg")
async def stream_camera():
    async def proxy_mjpeg():
        async with httpx.AsyncClient() as client:
            try:
                # Proxy to CARLA viewer web streamer at localhost:8080
                async with client.stream("GET", "http://127.0.0.1:8080/camera.mjpg", timeout=None) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except (httpx.ConnectError, httpx.TimeoutException):
                # If CARLA is not running, stream standby placeholder frames
                while True:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + STANDBY_FRAME + b"\r\n"
                    )
                    await asyncio.sleep(0.5)

    return StreamingResponse(
        proxy_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
```

---

## 5. Overview & Health Probes (`api/v1/overview.py` & `api/v1/health.py`)

### 5.1 Real Database Overview Aggregation (`GET /overview`)
```python
@router.get("/overview", response_model=Overview)
async def get_overview(session: AsyncSession = Depends(get_session)) -> Overview:
    # 1. Car state counts
    car_counts = await session.execute(
        select(Car.state, func.count(Car.id)).group_by(Car.state)
    )
    counts = dict(car_counts.all())

    # 2. Open assistance cases
    open_cases = await session.scalar(
        select(func.count(AssistanceCase.id)).where(AssistanceCase.status != "RESOLVED")
    )

    # 3. Pending proposals
    pending_proposals = await session.scalar(
        select(func.count(Proposal.id)).where(Proposal.status == "pending")
    )

    return Overview(
        plant="Plant 01",
        updated_at=utc_now(),
        cars=OverviewCars(
            total=sum(counts.values()),
            line_end=counts.get("line_end", 0),
            inspection_gate=counts.get("inspection_gate", 0),
            test_track=counts.get("test_track", 0),
            rework=counts.get("rework", 0),
            yard=counts.get("yard", 0),
            shipped=counts.get("shipped", 0),
        ),
        assistance_open=open_cases or 0,
        proposals_pending=pending_proposals or 0,
        attention=OverviewAttention(urgent=open_cases > 0, pending=pending_proposals > 0),
    )
```

---

## 6. End-to-End Integration: Turning Mock Mode Off

Once Sprint 5 is deployed:
1. Update frontend configuration in `marshal-fe/.env.development`:
   ```env
   VITE_MOCK_MODE=false
   VITE_API_BASE_URL=http://localhost:8000/api/v1
   ```
2. Start backend server:
   ```bash
   uvicorn marshal_api.main:app --host 0.0.0.0 --port 8000 --reload
   ```
3. Launch frontend client:
   ```bash
   cd marshal-fe && bun run dev
   ```
4. Verify complete end-to-end flows:
   - Vehicle movements on 2D map update from live DB records.
   - Live camera tag `<img src="http://localhost:8000/camera.mjpg" />` renders smoothly.
   - Remote assistance cases trigger modals in real-time via `/events`.
   - Supervisor actions successfully execute and commit to SQLite/PostgreSQL.

