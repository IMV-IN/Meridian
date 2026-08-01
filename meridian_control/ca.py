"""Built-in minimal CA (DESIGN.md 15.2, decision 14).

Issues Ed25519 node certificates bound to node identity. Node-cert profile only.
Behind this narrow interface so an enterprise PKI or cert-manager can replace it
without protocol changes. Keys are stored owner-only; KMS/HSM-backed storage is
a later option.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.x509.oid import NameOID


class NodeCA:
    def __init__(self, key: Ed25519PrivateKey, cert: x509.Certificate):
        self._key = key
        self._cert = cert

    @classmethod
    def load_or_create(cls, ca_dir: str | Path) -> "NodeCA":
        d = Path(ca_dir)
        key_path, cert_path = d / "ca_key.pem", d / "ca_cert.pem"
        if key_path.exists() and cert_path.exists():
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            assert isinstance(key, Ed25519PrivateKey)
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            return cls(key, cert)

        d.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "meridian-control-ca")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                key_encipherment=False, content_commitment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ), critical=True)
            .sign(key, None)
        )
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cls(key, cert)

    def issue_node_cert(self, node_id: str, node_public_key: bytes, lifetime_hours: int = 24) -> str:
        pub = Ed25519PublicKey.from_public_bytes(node_public_key)
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]))
            .issuer_name(self._cert.subject)
            .public_key(pub)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(hours=lifetime_hours))
            .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(f"meridian-node://{node_id}")]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(self._key, None)
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    def trust_bundle(self) -> str:
        return self._cert.public_bytes(serialization.Encoding.PEM).decode()
