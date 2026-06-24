"""SHA-256 hash chain for tamper-evident audit logs.

Each event is appended to a chain where:

    hash_n = SHA-256(hash_{n-1} || canonical_json(event_n))

Breaking a single link invalidates every subsequent hash, making
retrospective alteration detectable without trusting the storage layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List

# The genesis block uses a well-known seed so that verification is
# reproducible from scratch without any out-of-band data.
GENESIS_HASH = hashlib.sha256(b"meridian-genesis").hexdigest()


def _canonical(event: Dict[str, Any]) -> bytes:
    """Deterministic serialisation (sorted keys, no whitespace)."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass
class ChainEntry:
    """One link in the hash chain."""

    index: int
    event: Dict[str, Any]
    prev_hash: str
    hash: str


class HashChain:
    """Append-only SHA-256 hash chain."""

    def __init__(self) -> None:
        self._entries: List[ChainEntry] = []
        self._head: str = GENESIS_HASH

    @property
    def head(self) -> str:
        """Hash of the most recent entry (or genesis)."""
        return self._head

    @property
    def length(self) -> int:
        return len(self._entries)

    def append(self, event: Dict[str, Any]) -> ChainEntry:
        """Hash the event against the current head and append."""
        payload = self._head.encode() + _canonical(event)
        new_hash = hashlib.sha256(payload).hexdigest()
        entry = ChainEntry(
            index=len(self._entries),
            event=event,
            prev_hash=self._head,
            hash=new_hash,
        )
        self._entries.append(entry)
        self._head = new_hash
        return entry

    def entries(self) -> List[ChainEntry]:
        return list(self._entries)

    def verify(self) -> bool:
        """Walk the chain and return ``True`` iff every link is valid."""
        prev = GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            expected = hashlib.sha256(prev.encode() + _canonical(entry.event)).hexdigest()
            if entry.hash != expected:
                return False
            prev = entry.hash
        return True

    def reset(self) -> None:
        """Clear the chain (e.g. after a Merkle flush)."""
        self._entries.clear()
        self._head = GENESIS_HASH
