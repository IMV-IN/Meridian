"""Scan OpenAI-style chat request bodies for PII in message content."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence

from meridian.pii.detectors import apply_redactions, scan_text
from meridian.pii.types import Finding, ScanResult


def _iter_text_parts(content: Any) -> List[str]:
    """Extract plain-text fragments from message content (str or parts list)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        texts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text")
                if isinstance(t, str):
                    texts.append(t)
            elif isinstance(part, str):
                texts.append(part)
        return texts
    return []


def scan_messages(
    messages: Any,
    entities: Optional[Sequence[str]] = None,
) -> ScanResult:
    """Scan all user/system/assistant message text fields."""
    findings: List[Finding] = []
    if not isinstance(messages, list):
        return ScanResult(findings=findings)

    # Offsets are per-string; we don't need global offsets across messages for
    # redaction (we re-scan/replace per field). Collect findings for counts.
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for text in _iter_text_parts(msg.get("content")):
            findings.extend(scan_text(text, entities))
    return ScanResult(findings=findings)


def redact_messages(
    messages: Any,
    entities: Optional[Sequence[str]] = None,
) -> tuple[Any, ScanResult]:
    """Deep-copy messages and redact PII in place on the copy.

    Returns (new_messages, scan_result). Original list is not mutated.
    """
    if not isinstance(messages, list):
        return messages, ScanResult()

    new_messages = copy.deepcopy(messages)
    all_findings: List[Finding] = []

    for msg in new_messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            hits = scan_text(content, entities)
            if hits:
                msg["content"] = apply_redactions(content, hits)
                all_findings.extend(hits)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    t = part.get("text")
                    if isinstance(t, str):
                        hits = scan_text(t, entities)
                        if hits:
                            part["text"] = apply_redactions(t, hits)
                            all_findings.extend(hits)
                elif isinstance(part, str):
                    # rare: bare string in parts array
                    pass

    return new_messages, ScanResult(findings=all_findings)


def scan_and_maybe_redact_body(
    body: Dict[str, Any],
    *,
    policy: str,
    entities: Optional[Sequence[str]] = None,
) -> tuple[Dict[str, Any], ScanResult]:
    """Return (body, result). Body is a new dict only when redacting content."""
    messages = body.get("messages")
    if policy == "redact_and_replace":
        new_msgs, result = redact_messages(messages, entities)
        if result.empty:
            return body, result
        new_body = dict(body)
        new_body["messages"] = new_msgs
        return new_body, result

    result = scan_messages(messages, entities)
    return body, result
