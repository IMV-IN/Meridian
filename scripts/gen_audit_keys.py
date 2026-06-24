"""Generate the Ed25519 keypair used to sign audit Merkle roots.

Wraps :func:`meridian.audit.signer.generate_keypair`.  The private key is
mounted into the audit consumer (which signs each flushed Merkle root); the
public key is the *pinned, trusted* key the verifier checks signatures against
(see ``scripts/verify_audit_archive.py``).  Keep the private key secret — it is
the root of trust for non-repudiation.

Usage:
    python scripts/gen_audit_keys.py [--dir audit_keys] [--force]
"""

from __future__ import annotations

import argparse
import os
import sys

from meridian.audit.signer import generate_keypair


def main() -> int:
    p = argparse.ArgumentParser(description="Generate Meridian audit signing keypair")
    p.add_argument("--dir", default="audit_keys", help="Output directory")
    p.add_argument("--force", action="store_true", help="Overwrite existing keys")
    args = p.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    priv = os.path.join(args.dir, "audit_private.pem")
    pub = os.path.join(args.dir, "audit_public.pem")

    if (os.path.exists(priv) or os.path.exists(pub)) and not args.force:
        print(
            f"Keys already exist in {args.dir}/ — refusing to overwrite. "
            f"Use --force to regenerate (this INVALIDATES previously archived "
            f"signatures).",
            file=sys.stderr,
        )
        return 1

    generate_keypair(priv, pub)
    os.chmod(priv, 0o600)  # private key: owner read/write only
    print(f"Wrote private key -> {priv}")
    print(f"Wrote public key  -> {pub}")
    print("Mount the private key into the audit consumer; pin the public key in the verifier.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
