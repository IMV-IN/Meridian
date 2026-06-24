"""Merkle tree for batched audit log integrity proofs.

At flush time the audit consumer collects *N* leaf hashes (the chain hashes
from the hash chain) and builds a binary Merkle tree.  The root is then
signed with Ed25519 and the signed bundle is archived to S3 with Object Lock.

This module provides the tree construction and proof-generation logic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


def _hash_pair(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode()).hexdigest()


@dataclass
class MerkleTree:
    """Binary Merkle tree built from a list of leaf hashes."""

    leaves: List[str] = field(default_factory=list)
    _levels: List[List[str]] = field(default_factory=list, repr=False)

    @classmethod
    def build(cls, leaf_hashes: List[str]) -> MerkleTree:
        """Build a balanced Merkle tree from *leaf_hashes*.

        If the number of leaves is odd the last leaf is duplicated.
        """
        if not leaf_hashes:
            return cls(leaves=[], _levels=[[]])

        tree = cls(leaves=list(leaf_hashes))

        # Level 0 = leaves
        level: List[str] = list(leaf_hashes)
        tree._levels.append(level)

        while len(level) > 1 or (len(level) == 1 and len(tree._levels) == 1):
            # Duplicate last element if odd count for a balanced tree.
            if len(level) % 2 == 1:
                level = level + [level[-1]]
            next_level = [_hash_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
            tree._levels.append(next_level)
            level = next_level

        return tree

    @property
    def root(self) -> str:
        """Return the Merkle root (empty string if no leaves)."""
        if not self._levels or not self._levels[-1]:
            return ""
        return self._levels[-1][0]

    def audit_proof(self, leaf_index: int) -> Optional[List[Tuple[str, str]]]:
        """Return the Merkle audit proof for the leaf at *leaf_index*.

        Each element is a ``(direction, hash)`` pair where direction is
        ``"L"`` or ``"R"`` indicating which side the sibling sits on.

        Returns ``None`` if the index is out of range.
        """
        if leaf_index < 0 or leaf_index >= len(self.leaves):
            return None

        proof: List[Tuple[str, str]] = []
        idx = leaf_index

        for level in self._levels[:-1]:
            # Pad for odd-length levels.
            working = list(level)
            if len(working) % 2 == 1:
                working.append(working[-1])

            if idx % 2 == 0:
                sibling = working[idx + 1]
                proof.append(("R", sibling))
            else:
                sibling = working[idx - 1]
                proof.append(("L", sibling))
            idx //= 2

        return proof

    @staticmethod
    def verify_proof(leaf_hash: str, proof: List[Tuple[str, str]], expected_root: str) -> bool:
        """Verify an audit proof against *expected_root*."""
        current = leaf_hash
        for direction, sibling in proof:
            if direction == "L":
                current = _hash_pair(sibling, current)
            else:
                current = _hash_pair(current, sibling)
        return current == expected_root
