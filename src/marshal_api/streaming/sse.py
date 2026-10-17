"""Server-Sent Events stream of flow events (``GET /events``).

Backfills from the ``event_logs`` table from the client's ``since`` cursor, then
streams live messages off the bridge ``EventBus``. The frontend's ``EventSource``
reads the default ``message`` events, so data payloads are plain ``FlowEvent`` JSON.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from marshal_ai.dashboard import describe
from marshal_ai.dashboard import FLOWS as LIVE_FLOWS
from marshal_ai.events import Topic

from marshal_api.bridge import get_event_bus
from marshal_api.config import Settings, get_settings
from marshal_api.core.deps import get_current_tenant
from marshal_api.db.session import async_session_maker
from marshal_api.models import EventLog
from marshal_api.schemas.common import FlowEvent, Severity

log = logging.getLogger("marshal.streaming.sse")

router = APIRouter(tags=["streaming"])

BACKFILL_LIMIT = 200
LIVE_TIMEOUT_S = 15.0


def _level(value: object) -> Severity:
    try:
        return Severity(str(value))
    except ValueError:
        return Severity.INFO


def _at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _to_flow(row: EventLog) -> FlowEvent:
    return FlowEvent(
        seq=row.seq,
        at=row.recorded_at,
        flow=row.flow_number or 1,
        source=row.source,
        title=row.title,
        detail=row.detail,
        level=_level(row.level),
    )


def _decision_data(msg: dict) -> dict | None:
    """The structured half of a decision event, so a client does not parse the detail text."""
    if msg.get("type") not in ("route.choice", "parking.step"):
        return None
    return {k: msg[k] for k in ("vin", "choice", "slot", "probs", "by", "seconds", "decision")
            if msg.get(k) is not None}


def _live(msg: dict, seq: int, topic: Topic | None = None) -> FlowEvent:
    if topic is not None:
        # A live bus message is a domain payload (MissionDone, TrackStep, ...); marshal_ai's
        # dashboard already turns it into the flow's line. No keys are added to the payload.
        flow, _name = LIVE_FLOWS.get(topic, (1, ""))
        title, detail, level = describe(topic, msg)
        return FlowEvent(
            seq=seq,
            at=_at(msg.get("at")),
            flow=flow,
            source=str(msg.get("source") or topic.value),
            title=title or str(msg.get("type", "")),
            detail=detail,
            level=_level(level),
            data=_decision_data(msg),
        )
    return FlowEvent(
        seq=seq,
        at=_at(msg.get("at")),
        flow=int(msg.get("flow", 1) or 1),
        source=str(msg.get("source", "system")),
        title=str(msg.get("title", msg.get("type", ""))),
        detail=str(msg.get("detail", "")),
        level=_level(msg.get("level")),
        data=_decision_data(msg),
    )


async def _backfill(
    session: AsyncSession, tenant_id: str, since: int, limit: int = BACKFILL_LIMIT
) -> list[FlowEvent]:
    rows = (
        await session.execute(
            select(EventLog)
            .where(EventLog.tenant_id == tenant_id, EventLog.seq > since)
            .order_by(EventLog.seq.asc())
            .limit(limit)
        )
    ).scalars().all()
    return [_to_flow(row) for row in rows]


async def _events(
    request: Request, since: int, tenant_id: str, live: bool = False
) -> AsyncGenerator[dict, None]:
    last_seq = since
    if not live:
        async with async_session_maker() as session:
            history = await _backfill(session, tenant_id, since)
        for event in history:
            last_seq = max(last_seq, event.seq)
            yield {"id": str(event.seq), "data": event.model_dump_json()}

    queue: asyncio.Queue[tuple[Topic | None, dict]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    bus = get_event_bus()
    # shortcut: live mode has no backfill; Pub/Sub starts at the next event after subscribe
    for topic in Topic:
        def on_bus(msg: dict, topic: Topic = topic) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (topic, msg))

        bus.subscribe(topic, on_bus)

    while True:
        if await request.is_disconnected():
            break
        try:
            topic, msg = await asyncio.wait_for(queue.get(), timeout=LIVE_TIMEOUT_S)
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": ""}
            continue
        last_seq += 1
        yield {
            "id": str(last_seq),
            "data": _live(msg, last_seq, topic if live else None).model_dump_json(),
        }


@router.get("/events")
async def stream_events(
    request: Request,
    since: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    tenant_id: str = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
):
    # Browsers resend as Last-Event-ID on reconnect; the query param wins if both are present.
    if since == 0 and last_event_id and last_event_id.isdigit():
        since = int(last_event_id)
    live = settings.store == "gcp"
    return EventSourceResponse(_events(request, since, tenant_id, live), ping=15)
