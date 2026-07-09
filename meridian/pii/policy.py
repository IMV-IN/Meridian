"""Resolve and apply PII policies."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from meridian.pii.scan import scan_and_maybe_redact_body
from meridian.pii.types import POLICIES, PiiDecision


def resolve_policy(
    global_policy: str,
    key_override: Optional[str],
) -> str:
    """Per-key override wins when set and valid; else global."""
    if key_override and key_override in POLICIES:
        return key_override
    if global_policy in POLICIES:
        return global_policy
    return "audit_only"


def apply_pii_policy(
    body: Dict[str, Any],
    *,
    policy: str,
    entities: Optional[Sequence[str]] = None,
) -> PiiDecision:
    """Scan request body and enforce *policy*.

    - block: deny with message if any finding
    - redact_and_replace: return redacted body
    - redact_for_logs / audit_only: allow; counts only (forward raw)
    """
    if policy not in POLICIES:
        policy = "audit_only"

    new_body, result = scan_and_maybe_redact_body(
        body, policy=policy, entities=entities
    )
    counts = result.counts

    if result.empty:
        return PiiDecision(allowed=True, policy=policy, counts={}, body=body)

    if policy == "block":
        kinds = ", ".join(sorted(counts.keys()))
        return PiiDecision(
            allowed=False,
            policy=policy,
            counts=counts,
            body=body,
            message=f"Request blocked: PII detected ({kinds})",
        )

    # redact_and_replace may have mutated; others forward original body.
    out_body = new_body if policy == "redact_and_replace" else body
    return PiiDecision(
        allowed=True,
        policy=policy,
        counts=counts,
        body=out_body,
        message="",
    )
