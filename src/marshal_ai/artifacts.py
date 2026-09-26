"""Where the photos and clips behind a verdict are kept.

Every record already carries a `gs://` URI - a gate view, a track frame, the forward view at an
assist stop - but nothing was ever uploaded, so the design's promise that "every verdict can be
traced back to a photo" did not hold and the URIs pointed at nothing. That also made them
unusable as model input: Vertex AI returns 403 for a path that does not exist.

`MARSHAL_ARTIFACTS=gcs` uploads the bytes to the URI the record already names. The default is
`none`, which keeps the URI as a label and uploads nothing, so local runs need no credentials.

Uploads must never break a verdict: a failed upload is logged and the URI is still recorded.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

log = logging.getLogger("marshal.artifacts")

DEFAULT_PREFIX = "gs://marshal/"


class Artifacts(Protocol):
    def put(self, uri: str, data: bytes, content_type: str = "image/jpeg") -> str: ...


class NoArtifacts:
    """Records the URI without storing anything."""

    name = "none"

    def put(self, uri: str, data: bytes, content_type: str = "image/jpeg") -> str:
        return uri


def split_uri(uri: str) -> tuple[str | None, str]:
    """`gs://bucket/a/b.jpg` -> ("bucket", "a/b.jpg"); anything else -> (None, uri)."""
    if not uri.startswith("gs://"):
        return None, uri
    rest = uri[len("gs://"):]
    bucket, _, path = rest.partition("/")
    return (bucket or None), path


class GcsArtifacts:
    """Uploads to one bucket, rewriting the URI's bucket to the configured one.

    Records were written with a `gs://marshal/...` convention long before a bucket existed, and
    bucket names are globally unique, so the path is kept and the bucket is replaced.
    """

    name = "gcs"

    def __init__(self, bucket: str, project: str | None = None, client=None):
        self.bucket_name = bucket
        self._project = project
        self._client = client
        self._bucket = None
        self._lock = threading.Lock()

    def _ensure(self):
        with self._lock:
            if self._bucket is None:
                if self._client is None:
                    from google.cloud import storage
                    self._client = storage.Client(project=self._project)
                self._bucket = self._client.bucket(self.bucket_name)
            return self._bucket

    def put(self, uri: str, data: bytes, content_type: str = "image/jpeg") -> str:
        _bucket, path = split_uri(uri)
        if not path:
            log.warning("artifact %r has no object path; not uploaded", uri)
            return uri
        target = f"gs://{self.bucket_name}/{path}"
        try:
            blob = self._ensure().blob(path)
            blob.upload_from_string(data, content_type=content_type)
        except Exception as exc:                 # evidence is best-effort, verdicts are not
            log.warning("could not upload %s (%s); keeping the URI unstored", target, exc)
            return uri
        return target


def make_artifacts(kind: str, project: str | None = None,
                   bucket: str | None = None) -> Artifacts:
    if kind != "gcs":
        return NoArtifacts()
    name = bucket or (f"marshal-artifacts-{project}" if project else None)
    if not name:
        log.warning("MARSHAL_ARTIFACTS=gcs needs a bucket or a project; storing nothing")
        return NoArtifacts()
    return GcsArtifacts(name, project)
