"""Write the Workload Identity Federation credential file the CARLA host uses.

The host is an EC2 instance on AWS; it has no GCP service-account key. Google's auth libraries
read this external-account file, fetch the instance role's credentials from IMDS, and exchange
them at GCP STS for the configured service account:

    python3 scripts/write_wif_credentials.py \
      --audience //iam.googleapis.com/projects/<num>/locations/global/workloadIdentityPools/<pool>/providers/<provider> \
      --service-account marshal-api@<project>.iam.gserviceaccount.com \
      --output ~/.config/marshal/wif.json

Values come from `terraform output wif_audience` and `service_account_email` in marshal-tf/gcp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# The shape gcloud's `create-cred-config` produces for an AWS provider; google-auth reads these keys.
CREDENTIAL_SOURCE = {
    "environment_id": "aws1",
    "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
    "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
    "regional_cred_verification_url": "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
}


def build_credentials(audience: str, service_account: str) -> dict:
    if not audience.startswith("//iam.googleapis.com/"):
        raise ValueError("audience must look like //iam.googleapis.com/projects/...")
    if "@" not in service_account or not service_account.endswith(".iam.gserviceaccount.com"):
        raise ValueError("service-account must be a full <name>@<project>.iam.gserviceaccount.com email")
    return {
        "type": "external_account",
        "audience": audience,
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url":
            f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{service_account}:generateAccessToken",
        "credential_source": dict(CREDENTIAL_SOURCE),
    }


def write_credentials(audience: str, service_account: str, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_credentials(audience, service_account), indent=2) + "\n")
    output.chmod(0o600)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--output", type=Path, default=Path("~/.config/marshal/wif.json"))
    args = parser.parse_args(argv)
    path = write_credentials(args.audience, args.service_account,
                             Path(args.output).expanduser())
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
