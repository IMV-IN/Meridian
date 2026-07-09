"""PII detection & redaction — India entity pack (Milestone L).

Security invariants
-------------------
- Matched values are never stored on findings, never written to JSONL/audit,
  and never used as Prometheus label values.
- Detection runs on the **request** only (response body not scanned in v0.7).
- Disabled by default: zero work when ``pii.enabled`` is false.
"""

from __future__ import annotations

from meridian.pii.policy import apply_pii_policy, resolve_policy
from meridian.pii.types import ALL_ENTITIES, POLICIES, PiiDecision, ScanResult

__all__ = [
    "ALL_ENTITIES",
    "POLICIES",
    "PiiDecision",
    "ScanResult",
    "apply_pii_policy",
    "resolve_policy",
]
