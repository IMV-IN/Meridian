"""The built-in CA issues real Ed25519 node certificates chained to the CA."""

from __future__ import annotations

from conftest import NodeKey
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from meridian_control.ca import NodeCA


def test_issue_node_cert_binds_identity_and_chains_to_ca(tmp_path):
    ca = NodeCA.load_or_create(tmp_path / "ca")
    key = NodeKey()
    raw_pub = key.key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    pem = ca.issue_node_cert("node_abc", raw_pub, lifetime_hours=24)
    cert = x509.load_pem_x509_certificate(pem.encode())

    # Subject is the node identity, and the cert carries the node's public key.
    assert cert.subject.rfc4514_string() == "CN=node_abc"
    assert cert.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    ) == raw_pub

    # The CA signature verifies against the CA's public key (chain of trust).
    ca_cert = x509.load_pem_x509_certificate(ca.trust_bundle().encode())
    ca_cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes)


def test_ca_is_stable_across_reload(tmp_path):
    ca1 = NodeCA.load_or_create(tmp_path / "ca")
    ca2 = NodeCA.load_or_create(tmp_path / "ca")
    assert ca1.trust_bundle() == ca2.trust_bundle()
