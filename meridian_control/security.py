"""Security primitives for Meridian Cloud: keys, ids, signed values, rate limits."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional


def load_or_create_secret(configured: str, ca_dir: Path) -> bytes:
    """Server secret for HMACs. Prefer the configured value; otherwise persist a
    random key next to the CA (already a protected, backed-up directory)."""
    if configured:
        return configured.encode()
    path = Path(ca_dir) / "session.key"
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48).encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another worker won the race
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def keyed_hash(secret: bytes, value: str) -> str:
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(secret: bytes, payload: Dict[str, Any]) -> str:
    """Tamper-evident (not encrypted) value: base64(json).hmac."""
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = _b64e(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unsign(secret: bytes, value: str, max_age_s: float) -> Optional[Dict[str, Any]]:
    try:
        body, mac = value.split(".", 1)
    except ValueError:
        return None
    expected = _b64e(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or time.time() - float(payload.get("iat", 0)) > max_age_s:
        return None
    return payload


_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def normalize_email(value: str) -> Optional[str]:
    email = (value or "").strip().lower()
    if len(email) > 320 or not _EMAIL.match(email):
        return None
    return email


def safe_next(path: Optional[str], default: str = "/ui/") -> str:
    """Post-login redirect target: a same-origin path only (no open redirect). Rejects
    backslashes and every control character (browsers drop TAB/CR/LF from URLs, which
    turns "/\t/evil.com" into "//evil.com"), then requires that the value parses with
    no scheme and no host."""
    if not path or len(path) > 2048 or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return default
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in path):
        return default
    parts = urllib.parse.urlsplit(path)
    if parts.scheme or parts.netloc or parts.path.startswith("//"):
        return default
    return path


class SlidingWindowLimiter:
    """In-process sliding-window limiter. Correct for a single control-plane
    replica (the free/self-hosted deployment); use a shared store before
    running several replicas behind a load balancer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}

    def allow(self, key: str, limit: int, window_s: float, now: Optional[float] = None) -> bool:
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= t - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            if len(self._hits) > 50_000:  # bound memory under abuse
                for k in [k for k, v in self._hits.items() if not v][:10_000]:
                    del self._hits[k]
            return True
