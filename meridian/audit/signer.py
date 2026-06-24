"""Ed25519 signing for Merkle roots.

Uses ``cryptography`` for key generation and signing.  The audit consumer
signs each flushed Merkle root before archiving to S3.

Non-repudiation depends on the verifier checking the signature against a
**trusted, pinned public key** obtained out-of-band (e.g. ``public.pem``
distributed through a separate channel).  The public key embedded in the
bundle is kept only as a convenience/reference field — it is NEVER trusted on
its own, because an attacker could forge a root, re-sign it with their own
keypair, and embed their own public key.  :func:`verify_signed_root` therefore
requires the trusted key and rejects any bundle whose embedded key does not
match it.

The signature covers a canonical serialization binding ``merkle_root`` and
``batch_size`` together, so neither field can be tampered with independently.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger("meridian.audit.signer")


def generate_keypair(private_path: str, public_path: str) -> None:
    """Generate a fresh Ed25519 keypair and write PEM files."""
    private_key = Ed25519PrivateKey.generate()
    priv_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    Path(private_path).write_bytes(priv_pem)
    Path(public_path).write_bytes(pub_pem)
    logger.info("Generated Ed25519 keypair: %s / %s", private_path, public_path)


def load_private_key(path: str) -> Ed25519PrivateKey:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    pem_data = Path(path).read_bytes()
    key = load_pem_private_key(pem_data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"Expected Ed25519PrivateKey, got {type(key).__name__}")
    return key


def load_public_key(path: str) -> Ed25519PublicKey:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    pem_data = Path(path).read_bytes()
    key = load_pem_public_key(pem_data)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(f"Expected Ed25519PublicKey, got {type(key).__name__}")
    return key


@dataclass(frozen=True)
class SignedMerkleRoot:
    """A signed bundle ready for archival."""

    merkle_root: str
    batch_size: int
    signature_b64: str  # base64-encoded Ed25519 signature
    public_key_b64: str  # base64-encoded raw public key (untrusted reference only)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, data: str) -> SignedMerkleRoot:
        return cls(**json.loads(data))


class MerkleRootSigner:
    """Signs Merkle roots with an Ed25519 private key."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._pub_b64 = base64.b64encode(
            self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()

    @classmethod
    def from_file(cls, path: str) -> MerkleRootSigner:
        return cls(load_private_key(path))

    def sign(self, merkle_root: str, batch_size: int) -> SignedMerkleRoot:
        """Sign the (merkle_root, batch_size) binding and return a bundle."""
        sig = self._private_key.sign(_canonical_message(merkle_root, batch_size))
        return SignedMerkleRoot(
            merkle_root=merkle_root,
            batch_size=batch_size,
            signature_b64=base64.b64encode(sig).decode(),
            public_key_b64=self._pub_b64,
        )


def _canonical_message(merkle_root: str, batch_size: int) -> bytes:
    """Return the canonical, unambiguous message that the signature covers.

    Both fields are bound together via a sorted, separator-fixed JSON encoding
    so that neither ``merkle_root`` nor ``batch_size`` can be tampered with
    independently, and so concatenation ambiguities cannot arise.
    """
    return json.dumps(
        {"merkle_root": merkle_root, "batch_size": batch_size},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def verify_signed_root(
    signed: SignedMerkleRoot,
    trusted_public_key: Ed25519PublicKey,
) -> bool:
    """Verify a ``SignedMerkleRoot`` against a trusted, pinned public key.

    Authenticity (and thus non-repudiation) requires the verifier to supply the
    public key out-of-band.  The key embedded in the bundle
    (``signed.public_key_b64``) is treated as untrusted reference data: if it
    does not match ``trusted_public_key`` the bundle is rejected, defeating an
    attacker who forges a root and embeds their own keypair.

    Returns ``True`` only if the embedded key matches the trusted key AND the
    signature over the canonical ``(merkle_root, batch_size)`` message verifies
    against the trusted key.
    """
    try:
        trusted_raw = trusted_public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        embedded_raw = base64.b64decode(signed.public_key_b64)
        if embedded_raw != trusted_raw:
            logger.warning("Embedded public key does not match trusted key; rejecting")
            return False
        sig = base64.b64decode(signed.signature_b64)
        trusted_public_key.verify(
            sig, _canonical_message(signed.merkle_root, signed.batch_size)
        )
        return True
    except Exception:
        logger.warning("Signature verification failed", exc_info=True)
        return False
