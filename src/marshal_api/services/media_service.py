"""Resolve media ids to signed URLs, and stream the bytes behind them."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_api.core import gcs
from marshal_api.core.errors import ApiError
from marshal_api.models import MediaArtifact
from marshal_api.schemas import Media
from marshal_api.schemas.jobs import MediaKind

log = logging.getLogger("marshal.api.media")


class MediaService:
    def __init__(self, session: AsyncSession, tenant_id: str = "tenant-demo") -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def _artifact(self, media_id: str) -> MediaArtifact:
        artifact = (
            await self.session.execute(
                select(MediaArtifact).where(
                    MediaArtifact.tenant_id == self.tenant_id,
                    # A case stores the artifact's internal id (clip_media_id FK) while a
                    # media_id from elsewhere is the public one; accept both so evidence
                    # always resolves.
                    or_(
                        MediaArtifact.media_id == media_id,
                        MediaArtifact.id == media_id,
                    ),
                )
            )
        ).scalars().first()
        if artifact is None:
            raise ApiError(404, "media_not_found", f"Media {media_id} not found")
        if not artifact.available:
            raise ApiError(410, "media_unavailable", f"Media {media_id} is unavailable")
        return artifact

    async def resolve_media(self, media_id: str) -> Media:
        artifact = await self._artifact(media_id)
        return Media(
            media_id=artifact.media_id,
            kind=_kind(artifact.kind),
            content_type=artifact.content_type,
            url=self._url(artifact, media_id),
            available=True,
        )

    async def read_media(self, media_id: str) -> tuple[bytes, str]:
        """The bytes of an artifact, from Cloud Storage or a local file."""
        artifact = await self._artifact(media_id)
        uri = artifact.storage_uri
        if uri.startswith("gs://"):
            data = await asyncio.to_thread(gcs.download_bytes, uri)
        else:
            path = Path(uri)
            if not path.is_file():
                raise ApiError(404, "media_not_found", f"Media file {uri} is not on disk")
            data = await asyncio.to_thread(path.read_bytes)
        return data, artifact.content_type

    def _url(self, artifact: MediaArtifact, media_id: str) -> str:
        uri = artifact.storage_uri
        if uri.startswith("gs://"):
            signed = _signed_gcs_url(uri)
            if signed:
                return signed
            # No signer (SDK or credentials missing): let the API stream the object itself.
            return f"/api/v1/media/{media_id}/file"
        if "://" in uri:
            return uri
        return f"/api/v1/media/{media_id}/file"


def _kind(value: str) -> MediaKind:
    return MediaKind.IMAGE if value in ("image", "photo") else MediaKind.CLIP


def _signed_gcs_url(uri: str, ttl_minutes: int = 60) -> str | None:
    """A signed URL when the GCP SDK and credentials exist; None otherwise (best effort)."""
    try:
        from datetime import timedelta

        from google.cloud import storage

        bucket_name, _, blob_name = uri.removeprefix("gs://").partition("/")
        return storage.Client().bucket(bucket_name).blob(blob_name).generate_signed_url(
            expiration=timedelta(minutes=ttl_minutes)
        )
    except Exception:
        log.warning("Could not sign %s; falling back", uri)
        return None
