"""Pull OpenAI-style usage fields out of responses.

# ponytail: regex on stream chunks is enough; full SSE parser if providers diverge.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple

# Last-resort scan for "usage":{...} in SSE text.
_USAGE_RE = re.compile(rb'"usage"\s*:\s*(\{[^}]+\})')


def usage_from_dict(body: Any) -> Optional[Tuple[int, int]]:
    """Return (prompt_tokens, completion_tokens) or None."""
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if prompt < 0 or completion < 0:
        return None
    return prompt, completion


def usage_from_sse_bytes(buf: bytes) -> Optional[Tuple[int, int]]:
    """Best-effort: find a usage object in accumulated SSE payload."""
    # Prefer full JSON lines after "data: "
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
        found = usage_from_dict(obj)
        if found is not None:
            return found
    # Fallback regex
    m = _USAGE_RE.search(buf)
    if not m:
        return None
    try:
        usage = json.loads(m.group(1))
    except Exception:
        return None
    return usage_from_dict({"usage": usage})


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
