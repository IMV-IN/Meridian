"""Console read APIs: /meridian/whoami, /meridian/config, /meridian/budgets, /ui.

The console bootstraps from these, so the tests pin three properties:
authorization matches the existing ops surface, responses are allowlisted
(no raw keys / upstream credentials / file paths), and the SPA fallback
never escapes ``ui/dist``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

import meridian.api.main as main_mod
from meridian.api.console import budget_snapshot
from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.config.models import MeridianConfig
from meridian.usage.bucket import period_bucket
from meridian.usage.types import MeterKey

PLAIN_KEY = "mrdn_PlainKeyAAAAbbbbCCCCdddd1"
VIEWER_KEY = "mrdn_ViewerKeyAAAbbbbCCCCddd2"
ADMIN_KEY = "mrdn_AdminKeyAAAAbbbbCCCCddd3"
UPSTREAM_SECRET = "Bearer upstream-secret-do-not-leak"


def _cfg(auth: bool = True, budgets: bool = False) -> MeridianConfig:
    data: dict = {
        "backends": [
            {
                "name": "b1",
                "url": "http://127.0.0.1:9",
                "model": "m",
                "tags": ["general", "stable"],
                "auth_header": UPSTREAM_SECRET,
                "telemetry": {"type": "json", "url": "http://127.0.0.1:9/telemetry"},
            }
        ],
        "auth": {
            "enabled": auth,
            "keys": [
                {"key": PLAIN_KEY, "org_id": "acme", "team_id": "ml", "allowed_models": ["m"]},
                {"key": VIEWER_KEY, "org_id": "acme", "role": "viewer"},
                {"key": ADMIN_KEY, "org_id": "ops", "role": "admin", "key_id": "admin-1"},
            ],
        },
        "canary": {
            "enabled": True,
            "canary_tags": ["canary"],
            "stable_tags": ["stable"],
            "steps": [{"weight": 10, "duration_s": 60}, {"weight": 50}],
        },
        "isolation": {"mode": "dedicated", "pools": {"bank": ["bank-pool"]}},
        "rate_limit": {"enabled": True, "global": {"token_capacity": 100, "token_refill_rate": 10}},
    }
    if budgets:
        data["budgets"] = {
            "enabled": True,
            "store": "memory",
            "orgs": {"acme": {"daily": {"tokens": 1000, "requests": 10}}},
            "teams": {"acme/ml": {"monthly": {"tokens": 5000}}},
        }
    return MeridianConfig.from_dict(data)


async def _client(cfg: MeridianConfig) -> httpx.AsyncClient:
    await init_app(cfg, start_health=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    )


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ── whoami ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_whoami_open_mode_reports_auth_disabled():
    async with await _client(_cfg(auth=False)) as c:
        resp = await c.get("/meridian/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_enabled"] is False
    assert body["identity"] is None
    assert body["capabilities"] == {
        "view_ops": True, "reload": False, "manage_keys": False, "read_all_cost": False,
    }
    assert body["features"]["canary"] is True


@pytest.mark.asyncio
async def test_whoami_requires_valid_key_when_auth_on():
    async with await _client(_cfg()) as c:
        missing = await c.get("/meridian/whoami")
        unknown = await c.get("/meridian/whoami", headers=_bearer("mrdn_ZZZZZZZZZZZZZZZZZZZZ"))
    assert missing.status_code == 401
    assert unknown.status_code == 401


@pytest.mark.asyncio
async def test_whoami_plain_key_sees_own_identity_without_ops_rights():
    async with await _client(_cfg()) as c:
        resp = await c.get("/meridian/whoami", headers=_bearer(PLAIN_KEY))
    assert resp.status_code == 200
    body = resp.json()
    assert body["identity"]["org_id"] == "acme"
    assert body["identity"]["team_id"] == "ml"
    assert body["identity"]["key_id"] == PLAIN_KEY[:13]
    assert body["identity"]["allowed_models"] == ["m"]
    assert body["capabilities"]["view_ops"] is False
    assert PLAIN_KEY not in resp.text


@pytest.mark.asyncio
async def test_whoami_admin_capabilities():
    async with await _client(_cfg()) as c:
        resp = await c.get("/meridian/whoami", headers=_bearer(ADMIN_KEY))
    caps = resp.json()["capabilities"]
    assert caps == {"view_ops": True, "reload": True, "manage_keys": True, "read_all_cost": True}
    assert resp.json()["identity"]["key_id"] == "admin-1"


# ── config ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_gated_like_status():
    async with await _client(_cfg()) as c:
        missing = await c.get("/meridian/config")
        plain = await c.get("/meridian/config", headers=_bearer(PLAIN_KEY))
        viewer = await c.get("/meridian/config", headers=_bearer(VIEWER_KEY))
    assert missing.status_code == 401
    assert plain.status_code == 403
    assert viewer.status_code == 200


@pytest.mark.asyncio
async def test_config_summary_shape_and_redaction():
    async with await _client(_cfg()) as c:
        resp = await c.get("/meridian/config", headers=_bearer(VIEWER_KEY))
    body = resp.json()
    raw = resp.text
    # No raw keys, no upstream credential, no telemetry URL.
    for secret in (PLAIN_KEY, VIEWER_KEY, ADMIN_KEY, UPSTREAM_SECRET, "/telemetry"):
        assert secret not in raw
    assert body["gateway"]["strategy"] == "least_inflight"
    assert body["canary"]["enabled"] is True
    assert body["canary"]["steps"] == [
        {"weight": 10.0, "duration_s": 60.0}, {"weight": 50.0, "duration_s": None},
    ]
    assert body["isolation"] == {"mode": "dedicated", "pools": {"bank": ["bank-pool"]}}
    assert body["rate_limit"]["global"] == {"token_capacity": 100.0, "token_refill_rate": 10.0}
    assert body["auth"] == {"enabled": True, "key_count": 3, "keys_file_configured": False}
    assert body["backends"][0]["upstream_auth"] is True
    assert body["backends"][0]["telemetry"] == {"type": "json", "interval_s": 5.0}
    assert body["pii"]["entities"] == body["pii"]["supported_entities"]
    assert body["reloadable"] is False


@pytest.mark.asyncio
async def test_config_open_when_auth_disabled():
    async with await _client(_cfg(auth=False)) as c:
        resp = await c.get("/meridian/config")
    assert resp.status_code == 200
    assert resp.json()["auth"]["enabled"] is False


# ── budgets ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budgets_disabled_returns_empty():
    async with await _client(_cfg()) as c:
        resp = await c.get("/meridian/budgets", headers=_bearer(VIEWER_KEY))
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "rows": []}


@pytest.mark.asyncio
async def test_budgets_gated():
    async with await _client(_cfg(budgets=True)) as c:
        plain = await c.get("/meridian/budgets", headers=_bearer(PLAIN_KEY))
    assert plain.status_code == 403


@pytest.mark.asyncio
async def test_budget_snapshot_reports_consumption():
    await init_app(_cfg(budgets=True), start_health=False)
    state = get_state()
    now = datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)
    assert state.usage_meter is not None
    state.usage_meter.check_and_increment(
        [
            MeterKey("org", "acme", "daily", period_bucket("daily", now), "tokens", 1000),
            MeterKey("org", "acme", "daily", period_bucket("daily", now), "requests", 10),
        ],
        cost=250,
        now=now,
    )
    snap = budget_snapshot(state, now=now)
    assert snap["enabled"] is True
    rows = {(r["scope_level"], r["scope_id"], r["period"], r["metric"]): r for r in snap["rows"]}
    assert set(rows) == {
        ("org", "acme", "daily", "tokens"),
        ("org", "acme", "daily", "requests"),
        ("team", "acme/ml", "monthly", "tokens"),
    }
    tok = rows[("org", "acme", "daily", "tokens")]
    assert tok["consumed"] == 250
    assert tok["remaining"] == 750
    assert tok["utilization"] == 0.25
    assert tok["bucket"] == "2026-03-14"
    assert tok["resets_at"] == "2026-03-15T00:00:00Z"
    assert rows[("org", "acme", "daily", "requests")]["consumed"] == 1
    team = rows[("team", "acme/ml", "monthly", "tokens")]
    assert team["consumed"] == 0
    assert team["resets_at"] == "2026-04-01T00:00:00Z"


# ── /ui serving ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ui_falls_back_to_legacy_dashboard_without_build(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "_UI_DIST", tmp_path / "missing")
    async with await _client(_cfg(auth=False)) as c:
        root = await c.get("/ui")
        deep = await c.get("/ui/backends/b1")
    assert root.status_code == 200
    assert "Meridian Dashboard" in root.text
    assert deep.status_code == 200
    assert root.headers["x-content-type-options"] == "nosniff"


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Meridian Console</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log('ok')")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside dist")
    return dist


@pytest.mark.asyncio
async def test_ui_serves_built_console_with_spa_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "_UI_DIST", _fake_dist(tmp_path))
    async with await _client(_cfg(auth=True)) as c:
        root = await c.get("/ui")
        deep = await c.get("/ui/fleet/nodes/node_1")
        asset = await c.get("/ui/assets/app-abc123.js")
        icon = await c.get("/ui/favicon.svg")
        missing_asset = await c.get("/ui/assets/gone-999.js")
    assert "Meridian Console" in root.text
    assert root.headers["cache-control"] == "no-cache"
    assert "Meridian Console" in deep.text
    assert asset.status_code == 200
    assert asset.text == "console.log('ok')"
    assert "immutable" in asset.headers["cache-control"]
    assert icon.status_code == 200
    assert missing_asset.status_code == 404


@pytest.mark.asyncio
async def test_ui_path_traversal_never_escapes_dist(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "_UI_DIST", _fake_dist(tmp_path))
    async with await _client(_cfg(auth=False)) as c:
        for probe in ("../secret.txt", "..%2Fsecret.txt", "assets/../../secret.txt"):
            resp = await c.get(f"/ui/{probe}")
            assert "outside dist" not in resp.text, probe


def test_config_summary_is_json_serializable():
    # Guard: every value the summary emits must survive json.dumps (no
    # pydantic objects leaking through).
    import asyncio

    asyncio.run(init_app(_cfg(budgets=True), start_health=False))
    from meridian.api.console import config_summary

    json.dumps(config_summary(get_state()))
