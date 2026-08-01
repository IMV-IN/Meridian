from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meridian_control.ca import NodeCA
from meridian_control.config import ControlConfig
from meridian_control.db import make_session_factory
from meridian_control.service import ControlService


class MutableClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return MutableClock()


def make_service(tmp_path, clock) -> ControlService:
    cfg = ControlConfig(db_url=f"sqlite:///{tmp_path}/control.db", ca_dir=tmp_path / "ca", lease_ttl_seconds=30)
    session_factory = make_session_factory(cfg.db_url)
    ca = NodeCA.load_or_create(cfg.ca_dir)
    return ControlService(session_factory, ca, cfg, now=clock)


class NodeKey:
    """A node keypair with helpers mirroring the agent's identity operations."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()

    def public_b64url(self) -> str:
        raw = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def sign_b64url(self, message: bytes) -> str:
        return base64.urlsafe_b64encode(self.key.sign(message)).decode().rstrip("=")


@pytest.fixture
def node_key():
    return NodeKey()
