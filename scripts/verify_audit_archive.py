"""Verify the tamper-evident audit archive end-to-end.

Connects to the (MinIO or real) S3 bucket the audit consumer archives to and
proves the three properties unit tests can only mock:

  1. AUTHENTICITY  — every archived SignedMerkleRoot verifies against the
     PINNED public key (audit_keys/audit_public.pem).
  2. PINNING       — verification REJECTS a different (attacker) public key, so
     the embedded key is not self-trusted (finding #1).
  3. IMMUTABILITY  — the locked object version cannot be deleted while under
     Object Lock COMPLIANCE retention (finding #2).

Reuses meridian.audit.signer (verify_signed_root / load_public_key /
SignedMerkleRoot) — the exact code path a real auditor would use.

Usage (defaults target the local MinIO from docker-compose.audit.yaml):
    python scripts/verify_audit_archive.py \
        --endpoint http://localhost:9000 \
        --bucket meridian-audit \
        --prefix audit-logs/ \
        --public-key audit_keys/audit_public.pem

Exits 0 only if all archived roots verify, the wrong key is rejected, and a
locked version cannot be deleted.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meridian.audit.signer import (
    SignedMerkleRoot,
    load_public_key,
    verify_signed_root,
)


def _s3_client(endpoint: str, region: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        # Path-style is required for S3-compatible endpoints like MinIO.
        config=Config(s3={"addressing_style": "path"}),
    )


def _list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def main() -> int:
    p = argparse.ArgumentParser(description="Verify the Meridian audit archive")
    p.add_argument("--endpoint", default="http://localhost:9000")
    p.add_argument("--bucket", default="meridian-audit")
    p.add_argument("--prefix", default="audit-logs/")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--public-key", default="audit_keys/audit_public.pem")
    args = p.parse_args()

    # The wrong-key negative test below intentionally triggers a verification
    # failure; silence the signer's WARNING so expected rejections don't look
    # like errors in the output.
    logging.getLogger("meridian.audit.signer").setLevel(logging.ERROR)

    # MinIO root creds by default; honor real AWS creds if already in the env.
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")

    trusted_pub = load_public_key(args.public_key)
    s3 = _s3_client(args.endpoint, args.region)

    keys = _list_keys(s3, args.bucket, args.prefix)
    if not keys:
        print(
            f"No archived objects under s3://{args.bucket}/{args.prefix} — has a "
            f"flush happened yet? Generate more traffic and wait for the consumer "
            f"to log 'Archived to S3: ...'.",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(keys)} archived bundle(s) under s3://{args.bucket}/{args.prefix}\n")

    # 1 + 2. Authenticity against the pinned key, and rejection of a wrong key.
    wrong_pub = Ed25519PrivateKey.generate().public_key()
    all_ok = True
    for key in keys:
        body = s3.get_object(Bucket=args.bucket, Key=key)["Body"].read().decode()
        signed = SignedMerkleRoot.from_json(body)
        ok = verify_signed_root(signed, trusted_pub)
        rejected_wrong = not verify_signed_root(signed, wrong_pub)
        status = "PASS" if (ok and rejected_wrong) else "FAIL"
        if not (ok and rejected_wrong):
            all_ok = False
        print(
            f"  [{status}] {key}  root={signed.merkle_root[:16]}… "
            f"batch={signed.batch_size}  trusted_key={'ok' if ok else 'BAD'} "
            f"wrong_key={'rejected' if rejected_wrong else 'ACCEPTED(!)'}"
        )

    print()
    if all_ok:
        print("✓ AUTHENTICITY: all roots verify against the pinned key; wrong key rejected.")
    else:
        print("✗ AUTHENTICITY: one or more bundles failed verification.")

    # 3. Immutability: the locked object VERSION must not be deletable.
    sample = keys[0]
    versions = s3.list_object_versions(Bucket=args.bucket, Prefix=sample).get("Versions", [])
    version_id = next((v["VersionId"] for v in versions if v["Key"] == sample), None)

    immutable = False
    if version_id is None:
        print(
            f"✗ IMMUTABILITY: could not find an object version for {sample} "
            f"(is versioning/Object Lock enabled on the bucket?)."
        )
    else:
        try:
            s3.delete_object(Bucket=args.bucket, Key=sample, VersionId=version_id)
            print(
                f"✗ IMMUTABILITY: locked version of {sample} was DELETED — Object "
                f"Lock is NOT protecting the archive!"
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            immutable = code in ("AccessDenied", "InvalidRequest", "MethodNotAllowed")
            verdict = "✓" if immutable else "✗"
            print(
                f"{verdict} IMMUTABILITY: delete of locked version was refused "
                f"({code}) — archived roots are immutable under Object Lock."
            )

    print()
    if all_ok and immutable:
        print("ALL CHECKS PASSED — the audit archive is authentic and tamper-evident.")
        return 0
    print("ONE OR MORE CHECKS FAILED.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
