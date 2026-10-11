"""Pull OpenAI-style usage fields out of responses.

Structured SSE usage parsing with a nested-JSON fallback for truncated tails.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

# Last-resort scan for "usage":{...} in SSE text.
_USAGE_RE = re.compile(r'"usage"\s*:\s*')


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    # Missing telemetry is unknown, not a measured cache miss.
    cache_details_available: bool = False


def usage_from_dict(body: Any) -> Optional[Usage]:
    """Extract token accounting; malformed cache details leave cost usage valid."""
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if prompt < 0 or completion < 0:
        return None
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if isinstance(cached, int) and not isinstance(cached, bool) and 0 <= cached <= prompt:
        return Usage(prompt, completion, cached, True)
    return Usage(prompt, completion)


def usage_from_sse_bytes(buf: bytes) -> Optional[Usage]:
    """Best-effort: last usage object in SSE payload wins (final chunk preferred)."""
    found: Optional[Usage] = None
    for line in buf.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload in (b"", b"[DONE]"):
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        u = usage_from_dict(obj)
        if u is not None:
            found = u  # keep scanning — last wins
    if found is not None:
        return found
    # Tail buffers can begin in the middle of an event. raw_decode handles
    # nested prompt_tokens_details without the old first-closing-brace bug.
    text = buf.decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    for match in _USAGE_RE.finditer(text):
        try:
            usage, _ = decoder.raw_decode(text, match.end())
        except ValueError:
            continue
        parsed = usage_from_dict({"usage": usage})
        if parsed is not None:
            found = parsed
    return found


def compute_cost(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    prompt_per_1m: float,
    completion_per_1m: float,
) -> float:
    return (
        prompt_tokens * prompt_per_1m / 1_000_000.0
        + completion_tokens * completion_per_1m / 1_000_000.0
    )
