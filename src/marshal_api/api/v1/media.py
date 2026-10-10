"""Media resolution endpoint (signed URL / local path)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from marshal_api.api.v1 import _stubs
from marshal_api.core.deps import get_media_service
from marshal_api.schemas import Media
from marshal_api.services import MediaService

router = APIRouter(tags=["media"])


@router.get("/media/{media_id}", response_model=Media)
async def get_media(
    media_id: str,
    _service: MediaService = Depends(get_media_service),
) -> Media:
    return _stubs.media(media_id)
