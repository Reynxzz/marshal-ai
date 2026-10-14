"""Multipart MJPEG proxy for the CARLA viewer / plant CCTV.

Streams the configured upstream MJPEG feed and, when it is unreachable, keeps the
connection open with standby frames so the frontend ``<img>`` never errors.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse

from marshal_api.config import Settings, get_settings

log = logging.getLogger("marshal.streaming.camera")

router = APIRouter(tags=["streaming"])

BOUNDARY = "frame"
FRAME_DELAY_S = 0.5

# A 1x1 grey JPEG, used as the standby frame when no live feed is available.
STANDBY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "FBwcJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPDs0NDT/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAT8AABn/2Q=="
)


async def _standby_frames():
    while True:
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + STANDBY_JPEG + b"\r\n"
        await asyncio.sleep(FRAME_DELAY_S)


async def _proxy(url: str):
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise httpx.HTTPError(f"upstream status {response.status_code}")
                async for chunk in response.aiter_bytes():
                    yield chunk
    except Exception:
        log.warning("camera upstream %s unavailable; streaming standby frames", url)
        async for frame in _standby_frames():
            yield frame


@router.get("/camera/stream")
async def camera_stream(settings: Settings = Depends(get_settings)) -> StreamingResponse:
    return StreamingResponse(
        _proxy(settings.camera_stream_url),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
    )


@router.get("/camera/snapshot")
async def camera_snapshot(settings: Settings = Depends(get_settings)) -> Response:
    # shortcut: no per-frame grab; the standby JPEG stands in until a snapshot source exists
    return Response(content=STANDBY_JPEG, media_type="image/jpeg")
