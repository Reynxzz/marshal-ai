"""Media resolution endpoint (signed URL / local path) and byte streaming."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from marshal_api.core.deps import get_media_service
from marshal_api.schemas import Media
from marshal_api.services import MediaService

router = APIRouter(tags=["media"])


@router.get("/media/{media_id}", response_model=Media)
async def get_media(
    media_id: str,
    service: MediaService = Depends(get_media_service),
) -> Media:
    return await service.resolve_media(media_id)


@router.get("/media/{media_id}/file")
async def get_media_file(
    media_id: str,
    service: MediaService = Depends(get_media_service),
) -> Response:
    """Stream the artifact's bytes, so the dashboard can play a clip from Cloud Storage."""
    data, content_type = await service.read_media(media_id)
    return Response(content=data, media_type=content_type)
