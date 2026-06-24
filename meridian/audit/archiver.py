"""S3 Object Lock archiver for signed Merkle roots.

Uploads each ``SignedMerkleRoot`` as a JSON object to an S3 bucket with
Object Lock retention enabled (COMPLIANCE mode), making it legally
immutable for the configured retention period.

Requires the bucket to have been created with Object Lock enabled:

    aws s3api create-bucket --bucket meridian-audit \\
        --object-lock-enabled-for-object-lock-configuration

And a default retention policy:

    aws s3api put-object-lock-configuration --bucket meridian-audit \\
        --object-lock-configuration '{
            "ObjectLockEnabled": "Enabled",
            "Rule": {
                "DefaultRetention": {"Mode": "COMPLIANCE", "Days": 365}
            }
        }'
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from meridian.audit.signer import SignedMerkleRoot

logger = logging.getLogger("meridian.audit.archiver")


class S3Archiver:
    """Uploads signed bundles to S3 with Object Lock retention."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "audit-logs/",
        region: str = "ap-south-1",
        endpoint_url: str | None = None,
        retention_days: int = 365,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/"
        self._retention_days = retention_days
        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
            # S3-compatible endpoints (MinIO, etc.) require path-style addressing;
            # the default virtual-host style ("bucket.host") does not resolve.
            kwargs["config"] = Config(s3={"addressing_style": "path"})
        self._s3 = boto3.client("s3", **kwargs)

    def upload(self, signed: SignedMerkleRoot) -> str:
        """Upload and return the S3 key.

        Each object is locked independently in COMPLIANCE mode with an explicit
        retain-until date, so immutability does not silently depend on a bucket
        default retention policy that may be absent or misconfigured.

        Blocking (boto3 is synchronous) – callers on the event loop must run
        this in an executor (see ``AuditConsumer._flush``).
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        key = f"{self._prefix}{ts}_{signed.merkle_root[:16]}.json"

        retain_until = datetime.now(timezone.utc) + timedelta(days=self._retention_days)

        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=signed.to_json().encode(),
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
        )
        logger.info(
            "Archived signed Merkle root to s3://%s/%s (locked until %s)",
            self._bucket,
            key,
            retain_until.isoformat(),
        )
        return key
