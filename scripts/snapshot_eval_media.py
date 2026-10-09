"""Freeze run evidence into the bucket's eval/ prefix, for an evaluation dataset to point at.

Evidence paths are rewritten by every run of the same VIN (gate/VIN-S1/front.jpg is whatever the
last scene 1 took), so a dataset that named them would change under its labels. This copies them,
server-side, to eval/<version>/ where nothing writes.

    python scripts/snapshot_eval_media.py v1 gate/VIN-S1/ track/VIN-OK/ assist/AC-VIN-STUCK-1.jpg
"""

from __future__ import annotations

import argparse
import os

from google.cloud import storage


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="dataset version, e.g. v1")
    ap.add_argument("sources", nargs="+", help="object names or prefixes in the bucket")
    ap.add_argument("--bucket", default=os.getenv("MARSHAL_BUCKET")
                    or f"marshal-artifacts-{os.getenv('GOOGLE_CLOUD_PROJECT', '')}")
    ap.add_argument("--overwrite", action="store_true", help="replace a frozen copy that exists")
    args = ap.parse_args(argv)

    bucket = storage.Client().bucket(args.bucket)
    for source in args.sources:
        for blob in bucket.list_blobs(prefix=source):
            target = f"eval/{args.version}/{blob.name}"
            if bucket.blob(target).exists() and not args.overwrite:
                print(f"kept     gs://{args.bucket}/{target} (exists)")
                continue
            bucket.copy_blob(blob, bucket, target)
            print(f"frozen   gs://{args.bucket}/{target}  <- {blob.name} ({blob.updated:%Y-%m-%d %H:%M})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
