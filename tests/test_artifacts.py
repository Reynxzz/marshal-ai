"""Evidence storage must be best-effort: a failed upload may never break a verdict."""

from __future__ import annotations

import pytest

from marshal_ai.artifacts import GcsArtifacts, NoArtifacts, make_artifacts, split_uri


class _Blob:
    def __init__(self, bucket, path):
        self.bucket, self.path = bucket, path

    def upload_from_string(self, data, content_type=None):
        self.bucket.uploads.append((self.path, data, content_type))


class _Bucket:
    def __init__(self, name):
        self.name, self.uploads = name, []

    def blob(self, path):
        return _Blob(self, path)


class _Client:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, name):
        return self._bucket


class _AngryBucket(_Bucket):
    def blob(self, path):
        raise RuntimeError("storage unavailable")


@pytest.mark.parametrize(("uri", "expected"), [
    ("gs://marshal/gate/VIN-1/front.jpg", ("marshal", "gate/VIN-1/front.jpg")),
    ("gs://bucket-only", ("bucket-only", "")),
    ("/tmp/local.jpg", (None, "/tmp/local.jpg")),
])
def test_split_uri(uri, expected):
    assert split_uri(uri) == expected


def test_the_default_store_records_the_uri_without_uploading():
    store = NoArtifacts()

    assert store.put("gs://marshal/gate/VIN-1/front.jpg", b"x") == \
        "gs://marshal/gate/VIN-1/front.jpg"


def test_gcs_keeps_the_path_and_rewrites_the_bucket():
    """Records use a gs://marshal/... convention; bucket names are globally unique."""
    bucket = _Bucket("marshal-artifacts-proj")
    store = GcsArtifacts("marshal-artifacts-proj", client=_Client(bucket))

    uri = store.put("gs://marshal/gate/VIN-1/front.jpg", b"jpeg-bytes")

    assert uri == "gs://marshal-artifacts-proj/gate/VIN-1/front.jpg"
    assert bucket.uploads == [("gate/VIN-1/front.jpg", b"jpeg-bytes", "image/jpeg")]


def test_a_failed_upload_keeps_the_original_uri_and_does_not_raise():
    store = GcsArtifacts("marshal-artifacts-proj", client=_Client(_AngryBucket("b")))

    uri = store.put("gs://marshal/assist/AC-1.jpg", b"frame")

    assert uri == "gs://marshal/assist/AC-1.jpg", "a verdict must survive a storage outage"


def test_a_uri_with_no_object_path_is_not_uploaded():
    bucket = _Bucket("b")
    store = GcsArtifacts("b", client=_Client(bucket))

    assert store.put("gs://marshal", b"x") == "gs://marshal"
    assert bucket.uploads == []


@pytest.mark.parametrize("kind", ["none", "", "memory", "local"])
def test_only_gcs_stores_anything(kind):
    assert isinstance(make_artifacts(kind, project="p"), NoArtifacts)


def test_gcs_derives_the_bucket_from_the_project():
    store = make_artifacts("gcs", project="id-rd-ca-qais-jabbar")

    assert isinstance(store, GcsArtifacts)
    assert store.bucket_name == "marshal-artifacts-id-rd-ca-qais-jabbar"


def test_an_explicit_bucket_wins():
    assert make_artifacts("gcs", project="p", bucket="chosen").bucket_name == "chosen"


def test_gcs_without_a_project_or_bucket_degrades_instead_of_failing():
    assert isinstance(make_artifacts("gcs"), NoArtifacts)
