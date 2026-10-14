"""Read CARLA evidence photos and clips out of Google Cloud Storage.

The CARLA host uploads the frame behind each verdict when ``MARSHAL_ARTIFACTS=gcs``
(see ``marshal_ai.artifacts``), so the ``gs://bucket/object`` a record carries is
where the real bytes live. Gemini reads a still inline; a clip over the inline
limit is passed by URI, which Vertex fetches itself.
"""

from __future__ import annotations

import logging

from marshal_ai.artifacts import split_uri
from marshal_ai.llm import Media

from marshal_api.core.errors import ApiError

log = logging.getLogger("marshal.api.gcs")

# Vertex's inline request limit is about 20 MB; above this a clip goes by URI.
INLINE_MAX_BYTES = 15 * 1024 * 1024

_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def guess_mime(name: str) -> str:
    lower = name.lower()
    for suffix, mime in _MIME.items():
        if lower.endswith(suffix):
            return mime
    return "application/octet-stream"


def load_gcs_media(uri: str, client=None) -> Media:
    """The bytes (or the URI) of a ``gs://`` object, ready to hand to Gemini."""
    bucket_name, path = split_uri(uri)
    if not bucket_name or not path:
        raise ApiError(422, "invalid_media_uri", f"{uri!r} is not a gs://bucket/object URI")

    if client is None:
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise ApiError(
                503, "gcs_unavailable", "google-cloud-storage is not installed"
            ) from exc
        client = storage.Client()

    try:
        blob = client.bucket(bucket_name).blob(path)
        if not blob.exists():
            raise ApiError(404, "media_not_found", f"{uri} does not exist in Cloud Storage")
        blob.reload()
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(502, "gcs_read_failed", f"could not read {uri}: {exc}") from exc

    declared = blob.content_type
    mime = declared if declared and declared != "application/octet-stream" else guess_mime(path)
    size = blob.size or 0
    if mime.startswith("video/") and size > INLINE_MAX_BYTES:
        return Media(mime=mime, uri=uri, label="evidence clip")

    try:
        data = blob.download_as_bytes()
    except Exception as exc:
        raise ApiError(502, "gcs_read_failed", f"could not download {uri}: {exc}") from exc
    return Media(mime=mime, data=data, uri=uri, label="evidence clip")
