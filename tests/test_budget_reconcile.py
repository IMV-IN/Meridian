"""0.9.2: budget meter reconcile against actual backend usage."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from unittest.mock import MagicMock

import httpx
import pytest
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.api.pipeline import reconcile_budget_usage
from meridian.config.models import MeridianConfig
from meridian.usage import InMemoryUsageMeter, MeterKey
from meridian.usage.bucket import period_bucket
from mock_backend.server import app as mock_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Shared mock backend for e2e path
_mock_port = _free_port()
_cfg = uvicorn.Config(mock_app, host="127.0.0.1", port=_mock_port, log_level="error")
_srv = uvicorn.Server(_cfg)
threading.Thread(target=_srv.run, daemon=True).start()
for _ in range(50):
    try:
        httpx.get(f"http://127.0.0.1:{_mock_port}/v1/models", timeout=0.5)
        break
    except Exception:
        time.sleep(0.1)

_MOCK_URL = f"http://127.0.0.1:{_mock_port}"
KEY = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"


def _token_key(scope_id: str = "acme", cap: float = 10_000.0) -> MeterKey:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return MeterKey(
        scope_level="org",
        scope_id=scope_id,
        period="daily",
        period_bucket=period_bucket("daily", now),
        metric="tokens",
        cap=cap,
    )


def _req_key(scope_id: str = "acme", cap: float = 10_000.0) -> MeterKey:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return MeterKey(
        scope_level="org",
        scope_id=scope_id,
        period="daily",
        period_bucket=period_bucket("daily", now),
        metric="requests",
        cap=cap,
    )


def _state(
    meter: InMemoryUsageMeter,
    *,
    prefill: float = 1.0,
    decode: float = 4.0,
    budgets_enabled: bool = True,
) -> MagicMock:
    st = MagicMock()
    st.usage_meter = meter
    st.config.budgets.enabled = budgets_enabled
    st.config.gateway.prefill_weight = prefill
    st.config.gateway.decode_weight = decode
    return st


# ── Unit: reconcile_budget_usage ────────────────────────────────────────────


def test_reconcile_over_actual_charges_delta():
    meter = InMemoryUsageMeter()
    key = _token_key()
    meter.check_and_increment([key], cost=100.0)  # estimate
    st = _state(meter)
    # actual: 20 prompt + 10 completion * 4 = 20 + 40 = 60 → delta -40
    reconcile_budget_usage(
        st,
        meter_keys=[key],
        estimated_cost=100.0,
        prompt_tokens=20,
        completion_tokens=10,
    )
    assert meter.usage(key).consumed == pytest.approx(60.0)


def test_reconcile_under_estimate_refunds():
    meter = InMemoryUsageMeter()
    key = _token_key()
    meter.check_and_increment([key], cost=50.0)
    st = _state(meter, prefill=1.0, decode=1.0)
    # actual: 80 + 40 = 120 → delta +70
    reconcile_budget_usage(
        st,
        meter_keys=[key],
        estimated_cost=50.0,
        prompt_tokens=80,
        completion_tokens=40,
    )
    assert meter.usage(key).consumed == pytest.approx(120.0)


def test_reconcile_equal_noop():
    meter = InMemoryUsageMeter()
    key = _token_key()
    meter.check_and_increment([key], cost=42.0)
    st = _state(meter, prefill=1.0, decode=1.0)
    reconcile_budget_usage(
        st,
        meter_keys=[key],
        estimated_cost=42.0,
        prompt_tokens=20,
        completion_tokens=22,
    )
    assert meter.usage(key).consumed == pytest.approx(42.0)


def test_reconcile_skips_request_metric():
    meter = InMemoryUsageMeter()
    tok = _token_key()
    req = _req_key()
    meter.check_and_increment([tok, req], cost=10.0, requests=1)
    st = _state(meter, prefill=1.0, decode=1.0)
    reconcile_budget_usage(
        st,
        meter_keys=[tok, req],
        estimated_cost=10.0,
        prompt_tokens=5,
        completion_tokens=0,
    )
    assert meter.usage(tok).consumed == pytest.approx(5.0)
    assert meter.usage(req).consumed == pytest.approx(1.0)


def test_reconcile_noop_when_budgets_disabled():
    meter = InMemoryUsageMeter()
    key = _token_key()
    meter.check_and_increment([key], cost=10.0)
    st = _state(meter, budgets_enabled=False)
    reconcile_budget_usage(
        st,
        meter_keys=[key],
        estimated_cost=10.0,
        prompt_tokens=1,
        completion_tokens=1,
    )
    assert meter.usage(key).consumed == pytest.approx(10.0)


def test_reconcile_noop_empty_keys():
    meter = InMemoryUsageMeter()
    st = _state(meter)
    reconcile_budget_usage(
        st, meter_keys=[], estimated_cost=10.0, prompt_tokens=99, completion_tokens=1
    )


# ── E2E: live mock backend returns usage → meter matches actual cost ───────


@pytest.mark.asyncio
async def test_e2e_reconcile_after_non_stream():
    """Pre-flight reserves estimate; after response, meter matches actual usage cost."""
    cfg = MeridianConfig.from_dict({
        "gateway": {
            "strategy": "least_inflight",
            "prefill_weight": 1.0,
            "decode_weight": 4.0,
            "default_max_tokens": 16,
        },
        "auth": {
            "enabled": True,
            "keys": [{"key": KEY, "org_id": "acme"}],
        },
        "budgets": {
            "enabled": True,
            "store": "memory",
            "orgs": {"acme": {"daily": {"tokens": 1_000_000, "requests": 10_000}}},
        },
        "backends": [{
            "name": "mock",
            "url": _MOCK_URL,
            "engine": "mock",
            "model": "demo-model",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    st = get_state()
    assert st.usage_meter is not None

    body = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "hello world"}],
        "max_tokens": 16,
        "stream": False,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app),
        base_url="http://test",
    ) as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json=body,
        )
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    prompt_t = usage["prompt_tokens"]
    completion_t = usage["completion_tokens"]
    expected = prompt_t * 1.0 + completion_t * 4.0

    from datetime import datetime, timezone

    from meridian.auth.models import IdentityContext
    from meridian.usage import build_meter_keys

    mkeys = build_meter_keys(
        IdentityContext(org_id="acme"),
        st.config.budgets,
        now=datetime.now(timezone.utc),
    )
    token_keys = [k for k in mkeys if k.metric == "tokens"]
    assert len(token_keys) == 1
    assert st.usage_meter.usage(token_keys[0]).consumed == pytest.approx(expected)

    req_keys = [k for k in mkeys if k.metric == "requests"]
    assert len(req_keys) == 1
    assert st.usage_meter.usage(req_keys[0]).consumed == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_e2e_no_reconcile_on_upstream_failure():
    """Dead backend keeps pre-flight estimate (no refund on failure)."""
    dead = _free_port()
    cfg = MeridianConfig.from_dict({
        "gateway": {
            "strategy": "least_inflight",
            "prefill_weight": 1.0,
            "decode_weight": 1.0,
            "default_max_tokens": 8,
        },
        "auth": {
            "enabled": True,
            "keys": [{"key": KEY, "org_id": "acme"}],
        },
        "budgets": {
            "enabled": True,
            "store": "memory",
            "orgs": {"acme": {"daily": {"tokens": 1_000_000}}},
        },
        "backends": [{
            "name": "dead",
            "url": f"http://127.0.0.1:{dead}",
            "engine": "mock",
            "model": "demo",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    st = get_state()

    body = {
        "model": "demo",
        "messages": [{"role": "user", "content": "x"}],
        "max_tokens": 8,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app),
        base_url="http://test",
    ) as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json=body,
        )
    assert resp.status_code == 502

    from datetime import datetime, timezone

    from meridian.auth.models import IdentityContext
    from meridian.router.token_estimator import estimate_prompt_tokens, extract_max_tokens
    from meridian.usage import build_meter_keys

    prompt = estimate_prompt_tokens(body["messages"])
    max_tok = extract_max_tokens(body, 8)
    estimate = prompt * 1.0 + max_tok * 1.0
    mkeys = build_meter_keys(
        IdentityContext(org_id="acme"),
        st.config.budgets,
        now=datetime.now(timezone.utc),
    )
    tok = [k for k in mkeys if k.metric == "tokens"][0]
    assert st.usage_meter is not None
    assert st.usage_meter.usage(tok).consumed == pytest.approx(estimate)
