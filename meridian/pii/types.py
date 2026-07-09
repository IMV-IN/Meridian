"""Shared types for the PII subsystem.

Security rule: :class:`Finding` must never store the matched raw value — only
entity type, span offsets, and a redaction-ready placeholder. Callers log
counts by type only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Supported entity type ids (stable metric labels).
ENTITY_AADHAAR = "aadhaar"
ENTITY_PAN = "pan"
ENTITY_GSTIN = "gstin"
ENTITY_IFSC = "ifsc"
ENTITY_UPI = "upi"
ENTITY_PHONE = "phone_in"

ALL_ENTITIES = (
    ENTITY_AADHAAR,
    ENTITY_PAN,
    ENTITY_GSTIN,
    ENTITY_IFSC,
    ENTITY_UPI,
    ENTITY_PHONE,
)

POLICIES = frozenset({
    "block",
    "redact_and_replace",
    "redact_for_logs",
    "audit_only",
})


@dataclass(frozen=True)
class Finding:
    """One detection. **Never** carry the matched secret value."""

    entity: str
    start: int
    end: int
    # Safe substitute for redact_and_replace (e.g. XXXX-XXXX-1234).
    redaction: str


@dataclass
class ScanResult:
    findings: List[Finding] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.findings:
            out[f.entity] = out.get(f.entity, 0) + 1
        return out

    @property
    def empty(self) -> bool:
        return not self.findings


@dataclass
class PiiDecision:
    """Outcome of applying a policy to a request body."""

    allowed: bool
    policy: str
    counts: Dict[str, int]
    # Mutated body when policy is redact_and_replace; else same reference.
    body: Optional[dict] = None
    message: str = ""
