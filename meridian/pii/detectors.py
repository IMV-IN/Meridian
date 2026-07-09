"""Regex-first India entity detectors.

Findings never include the raw matched string as a stored field — only spans
and a redaction placeholder derived at detect time.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

from meridian.pii.types import (
    ALL_ENTITIES,
    ENTITY_AADHAAR,
    ENTITY_GSTIN,
    ENTITY_IFSC,
    ENTITY_PAN,
    ENTITY_PHONE,
    ENTITY_UPI,
    Finding,
)
from meridian.pii.verhoeff import verhoeff_validate

# Aadhaar: 12 digits, optional spaces/hyphens between groups of 4.
_AADHAAR_RE = re.compile(
    r"(?<!\d)(\d{4})[\s\-]?(\d{4})[\s\-]?(\d{4})(?!\d)"
)

# PAN: 5 letters + 4 digits + 1 letter (case-insensitive in text).
_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", re.IGNORECASE)

# GSTIN: 2-digit state + PAN + entity code + Z + check char (15 chars).
_GSTIN_RE = re.compile(
    r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b",
    re.IGNORECASE,
)

# IFSC: 4 letters + 0 + 6 alphanumeric.
_IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", re.IGNORECASE)

# UPI VPA: local-part @ handle (conservative charset).
_UPI_RE = re.compile(
    r"\b([a-zA-Z0-9._\-]{2,256}@[a-zA-Z][a-zA-Z0-9.\-]{1,64})\b"
)

# Indian mobile: optional +91 / 91 / 0, then 10 digits starting 6–9.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?91[\s\-]?|0)?([6-9]\d{9})(?!\d)"
)


def _mask_keep_last(digits: str, last: int = 4, group: int = 4) -> str:
    """Mask all but last *last* digits; optional grouping for Aadhaar style."""
    if len(digits) <= last:
        return "X" * len(digits)
    hidden = "X" * (len(digits) - last)
    shown = digits[-last:]
    combined = hidden + shown
    if group > 0:
        parts = [combined[i : i + group] for i in range(0, len(combined), group)]
        return "-".join(parts)
    return combined


def detect_aadhaar(text: str) -> List[Finding]:
    out: List[Finding] = []
    for m in _AADHAAR_RE.finditer(text):
        digits = m.group(1) + m.group(2) + m.group(3)
        if not verhoeff_validate(digits):
            continue
        out.append(
            Finding(
                entity=ENTITY_AADHAAR,
                start=m.start(),
                end=m.end(),
                redaction=_mask_keep_last(digits, last=4, group=4),
            )
        )
    return out


def detect_pan(text: str) -> List[Finding]:
    out: List[Finding] = []
    for m in _PAN_RE.finditer(text):
        raw = m.group(1).upper()
        # Mask middle 4 digits: ABCDE****F
        redaction = raw[:5] + "****" + raw[-1]
        out.append(
            Finding(entity=ENTITY_PAN, start=m.start(), end=m.end(), redaction=redaction)
        )
    return out


def detect_gstin(text: str) -> List[Finding]:
    out: List[Finding] = []
    for m in _GSTIN_RE.finditer(text):
        raw = m.group(1).upper()
        # Keep state code + last char; mask middle.
        redaction = raw[:2] + ("X" * 12) + raw[-1]
        out.append(
            Finding(entity=ENTITY_GSTIN, start=m.start(), end=m.end(), redaction=redaction)
        )
    return out


def detect_ifsc(text: str) -> List[Finding]:
    out: List[Finding] = []
    for m in _IFSC_RE.finditer(text):
        raw = m.group(1).upper()
        redaction = raw[:4] + "0" + ("X" * 6)
        out.append(
            Finding(entity=ENTITY_IFSC, start=m.start(), end=m.end(), redaction=redaction)
        )
    return out


def detect_upi(text: str) -> List[Finding]:
    out: List[Finding] = []
    for m in _UPI_RE.finditer(text):
        local, _, host = m.group(1).partition("@")
        if not host:
            continue
        # Avoid eating emails that look like corp domains with a TLD dot in host
        # for very short hosts (ok); require no spaces (already).
        redaction = (local[:2] + "***" if len(local) > 2 else "***") + "@" + host
        out.append(
            Finding(entity=ENTITY_UPI, start=m.start(), end=m.end(), redaction=redaction)
        )
    return out


def detect_phone_in(text: str) -> List[Finding]:
    out: List[Finding] = []
    for m in _PHONE_RE.finditer(text):
        digits = m.group(1)
        redaction = "XXXXXX" + digits[-4:]
        out.append(
            Finding(
                entity=ENTITY_PHONE,
                start=m.start(),
                end=m.end(),
                redaction=redaction,
            )
        )
    return out


_DETECTORS = {
    ENTITY_AADHAAR: detect_aadhaar,
    ENTITY_PAN: detect_pan,
    ENTITY_GSTIN: detect_gstin,
    ENTITY_IFSC: detect_ifsc,
    ENTITY_UPI: detect_upi,
    ENTITY_PHONE: detect_phone_in,
}


def scan_text(text: str, entities: Sequence[str] | None = None) -> List[Finding]:
    """Run enabled detectors on *text*; return non-overlapping-preferring list.

    Overlaps: keep earlier start, then longer span (simple left-to-right resolve).
    """
    if not text:
        return []
    wanted = set(entities) if entities else set(ALL_ENTITIES)
    raw: List[Finding] = []
    for ent in ALL_ENTITIES:
        if ent not in wanted:
            continue
        raw.extend(_DETECTORS[ent](text))
    if not raw:
        return []
    raw.sort(key=lambda f: (f.start, -(f.end - f.start)))
    resolved: List[Finding] = []
    cursor = -1
    for f in raw:
        if f.start < cursor:
            continue
        resolved.append(f)
        cursor = f.end
    return resolved


def apply_redactions(text: str, findings: Iterable[Finding]) -> str:
    """Replace each finding span with its redaction (right-to-left)."""
    ordered = sorted(findings, key=lambda f: f.start, reverse=True)
    out = text
    for f in ordered:
        out = out[: f.start] + f.redaction + out[f.end :]
    return out
