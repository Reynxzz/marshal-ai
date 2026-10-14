"""Read CARLA evidence photos and clips out of Google Cloud Storage.

The CARLA host uploads the frame behind each verdict when ``MARSHAL_ARTIFACTS=gcs``
(see ``marshal_ai.artifacts``), so the ``gs://bucket/object`` a record carries is
where the real bytes live. Gemini reads a still inline; a clip over the inline
limit is passed by URI, which Vertex fetches itself. ``download_bytes`` backs the
media endpoint that streams the same object to the browser.
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


def _blob(uri: str, client=None):
    """The existing GCS blob for ``gs://bucket/object``, or an ApiError."""
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
        try:
            client = storage.Client()
        except Exception as exc:
            # No GOOGLE_APPLICATION_CREDENTIALS / ADC: a clean 503, not a 500.
            raise ApiError(
                503, "gcs_unavailable", f"no usable GCS credentials: {exc}"
            ) from exc
    try:
        blob = client.bucket(bucket_name).blob(path)
        if not blob.exists():
            raise ApiError(404, "media_not_found", f"{uri} does not exist in Cloud Storage")
        blob.reload()
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(502, "gcs_read_failed", f"could not read {uri}: {exc}") from exc
    return blob


def _mime_of(blob, path: str) -> str:
    declared = blob.content_type
    return declared if declared and declared != "application/octet-stream" else guess_mime(path)


def _download(blob, uri: str) -> bytes:
    try:
        return blob.download_as_bytes()
    except Exception as exc:
        raise ApiError(502, "gcs_read_failed", f"could not download {uri}: {exc}") from exc


def load_gcs_media(uri: str, client=None) -> Media:
    """The bytes (or the URI) of a ``gs://`` object, ready to hand to Gemini."""
    blob = _blob(uri, client)
    _, path = split_uri(uri)
    mime = _mime_of(blob, path)
    if mime.startswith("video/") and (blob.size or 0) > INLINE_MAX_BYTES:
        return Media(mime=mime, uri=uri, label="evidence clip")
    return Media(mime=mime, data=_download(blob, uri), uri=uri, label="evidence clip")


def download_bytes(uri: str, client=None) -> bytes:
    """The whole object, for the media endpoint that streams it to the browser."""
    return _download(_blob(uri, client), uri)
