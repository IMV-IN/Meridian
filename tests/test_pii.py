"""Milestone L: India PII pack — unit detectors + policy integration."""

from __future__ import annotations

import socket
from typing import Optional

import httpx
import pytest
from pydantic import ValidationError

from meridian.api.main import app as meridian_app
from meridian.api.main import init_app
from meridian.config.models import MeridianConfig, PiiConfig
from meridian.pii.detectors import apply_redactions, scan_text
from meridian.pii.policy import apply_pii_policy, resolve_policy
from meridian.pii.types import ENTITY_AADHAAR, ENTITY_PAN
from meridian.pii.verhoeff import verhoeff_validate

# Valid Verhoeff 12-digit numbers (synthetic; not real Aadhaar enrolments).
VALID_AADHAAR = "234567890124"
VALID_AADHAAR_SPACED = "2345 6789 0124"
INVALID_AADHAAR = "123456789012"  # fails Verhoeff


# ── Verhoeff ────────────────────────────────────────────────────────────────


def test_verhoeff_accepts_valid():
    assert verhoeff_validate(VALID_AADHAAR) is True


def test_verhoeff_rejects_invalid():
    assert verhoeff_validate(INVALID_AADHAAR) is False
    assert verhoeff_validate("abcdefghijkl") is False
    assert verhoeff_validate("") is False


# ── Detectors ───────────────────────────────────────────────────────────────


def test_aadhaar_detects_valid_only():
    hits = scan_text(f"my id is {VALID_AADHAAR} ok")
    assert len(hits) == 1
    assert hits[0].entity == ENTITY_AADHAAR
    assert "2345" not in hits[0].redaction or hits[0].redaction.endswith("0124")
    # redaction must not contain full number
    assert VALID_AADHAAR not in hits[0].redaction


def test_aadhaar_spaced_valid():
    hits = scan_text(VALID_AADHAAR_SPACED)
    assert len(hits) == 1


def test_aadhaar_false_positive_guard():
    hits = scan_text(f"random {INVALID_AADHAAR} digits")
    assert hits == []


def test_pan_detect():
    text = "PAN is ABCDE1234F for tax"
    hits = scan_text(text)
    assert any(h.entity == ENTITY_PAN for h in hits)
    pan = next(h for h in hits if h.entity == ENTITY_PAN)
    assert "1234" not in pan.redaction
    assert pan.redaction.startswith("ABCDE")


def test_gstin_ifsc_upi_phone():
    text = (
        "GSTIN 22AAAAA0000A1Z5 IFSC HDFC0001234 "
        "pay me@oksbi phone 9876543210"
    )
    hits = scan_text(text)
    ents = {h.entity for h in hits}
    assert "gstin" in ents
    assert "ifsc" in ents
    assert "upi" in ents
    assert "phone_in" in ents


def test_redact_applies_placeholders():
    text = f"aadhaar {VALID_AADHAAR}"
    hits = scan_text(text)
    out = apply_redactions(text, hits)
    assert VALID_AADHAAR not in out
    assert "XXXX" in out or "X" in out


def test_entities_filter():
    text = f"{VALID_AADHAAR} and ABCDE1234F"
    only_pan = scan_text(text, entities=["pan"])
    assert all(h.entity == ENTITY_PAN for h in only_pan)
    assert len(only_pan) == 1


# ── Policy ──────────────────────────────────────────────────────────────────


def test_resolve_policy_override():
    assert resolve_policy("block", "audit_only") == "audit_only"
    assert resolve_policy("block", None) == "block"
    assert resolve_policy("block", "nope") == "block"


def test_policy_block():
    body = {
        "model": "demo",
        "messages": [{"role": "user", "content": f"id {VALID_AADHAAR}"}],
    }
    d = apply_pii_policy(body, policy="block")
    assert d.allowed is False
    assert d.counts.get("aadhaar") == 1
    assert VALID_AADHAAR not in d.message


def test_policy_redact_and_replace():
    body = {
        "model": "demo",
        "messages": [{"role": "user", "content": f"id {VALID_AADHAAR}"}],
    }
    d = apply_pii_policy(body, policy="redact_and_replace")
    assert d.allowed is True
    assert d.body is not None
    content = d.body["messages"][0]["content"]
    assert VALID_AADHAAR not in content
    # Original body untouched
    assert VALID_AADHAAR in body["messages"][0]["content"]


def test_policy_audit_only_forwards_raw():
    body = {
        "model": "demo",
        "messages": [{"role": "user", "content": f"id {VALID_AADHAAR}"}],
    }
    d = apply_pii_policy(body, policy="audit_only")
    assert d.allowed is True
    assert d.body is body
    assert d.counts["aadhaar"] == 1


def test_policy_clean_body_noop():
    body = {
        "model": "demo",
        "messages": [{"role": "user", "content": "hello world"}],
    }
    d = apply_pii_policy(body, policy="block")
    assert d.allowed is True
    assert d.counts == {}


def test_pii_config_rejects_bad_policy():
    with pytest.raises(ValidationError):
        PiiConfig(policy="delete_all")


# ── Integration ─────────────────────────────────────────────────────────────


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _client(pii: dict, auth: Optional[dict] = None) -> httpx.AsyncClient:
    cfg_dict: dict = {
        "pii": pii,
        "backends": [{
            "name": "dead",
            "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock",
            "model": "demo",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    }
    if auth is not None:
        cfg_dict["auth"] = auth
    await init_app(MeridianConfig.from_dict(cfg_dict), start_health=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_disabled_pii_is_noop():
    async with await _client({"enabled": False}) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "demo",
                "messages": [{"role": "user", "content": f"aadhaar {VALID_AADHAAR}"}],
            },
        )
    assert resp.status_code == 502  # reached dead backend


@pytest.mark.asyncio
async def test_block_returns_400():
    async with await _client({"enabled": True, "policy": "block"}) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "demo",
                "messages": [{"role": "user", "content": f"aadhaar {VALID_AADHAAR}"}],
            },
        )
    assert resp.status_code == 400
    assert "PII" in resp.json()["error"]["message"]
    assert VALID_AADHAAR not in resp.text


@pytest.mark.asyncio
async def test_redact_still_forwards():
    async with await _client({"enabled": True, "policy": "redact_and_replace"}) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "demo",
                "messages": [{"role": "user", "content": f"aadhaar {VALID_AADHAAR}"}],
            },
        )
    # Redacted body still routes → dead backend 502
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_per_key_policy_override_block():
    key = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
    async with await _client(
        {"enabled": True, "policy": "audit_only"},
        auth={
            "enabled": True,
            "keys": [{
                "key": key,
                "org_id": "acme",
                "pii_policy": "block",
            }],
        },
    ) as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "demo",
                "messages": [{"role": "user", "content": f"aadhaar {VALID_AADHAAR}"}],
            },
        )
    assert resp.status_code == 400
