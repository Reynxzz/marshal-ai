from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from write_wif_credentials import build_credentials, write_credentials  # noqa: E402

AUDIENCE = ("//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/"
            "aws-carla-host/providers/aws-ec2")
SERVICE_ACCOUNT = "marshal-api@example.iam.gserviceaccount.com"


def test_builds_external_account_credentials() -> None:
    credentials = build_credentials(AUDIENCE, SERVICE_ACCOUNT)

    assert credentials["type"] == "external_account"
    assert credentials["audience"] == AUDIENCE
    assert credentials["subject_token_type"] == "urn:ietf:params:aws:token-type:aws4_request"
    assert credentials["service_account_impersonation_url"].endswith(
        f"{SERVICE_ACCOUNT}:generateAccessToken")
    assert credentials["credential_source"]["environment_id"] == "aws1"


@pytest.mark.parametrize("audience,service_account", [
    ("projects/1", SERVICE_ACCOUNT),
    (AUDIENCE, "not-an-email"),
    (AUDIENCE, "sa@example.com"),
])
def test_rejects_malformed_input(audience, service_account) -> None:
    with pytest.raises(ValueError):
        build_credentials(audience, service_account)


def test_writes_private_json_file(tmp_path) -> None:
    path = write_credentials(AUDIENCE, SERVICE_ACCOUNT, tmp_path / "nested" / "wif.json")

    assert path.read_text().startswith("{")
    assert path.stat().st_mode & 0o777 == 0o600
