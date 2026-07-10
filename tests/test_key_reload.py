"""Milestone N: keys_file load + atomic key reload."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.api.reload import reload_keys
from meridian.auth.keys import build_key_index, load_keys_from_file
from meridian.config.models import AuthConfig, KeyConfig, MeridianConfig

KEY_A = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_B = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
KEY_OPS = "mrdn_1Aa2Bb3Cc4Dd5Ee6Ff7Gg8Hh"


def test_load_keys_from_file(tmp_path: Path):
    p = tmp_path / "keys.yaml"
    p.write_text(yaml.dump({"keys": [{"key": KEY_A, "org_id": "acme"}]}))
    keys = load_keys_from_file(str(p))
    assert len(keys) == 1
    assert keys[0].org_id == "acme"


def test_build_index_merges_file(tmp_path: Path):
    p = tmp_path / "keys.yaml"
    p.write_text(yaml.dump({"keys": [{"key": KEY_B, "org_id": "globex"}]}))
    auth = AuthConfig(
        enabled=True,
        keys=[KeyConfig(key=KEY_A, org_id="acme")],
        keys_file=str(p),
    )
    idx = build_key_index(auth)
    assert set(idx) == {KEY_A, KEY_B}


def test_build_index_rejects_dup_across_file(tmp_path: Path):
    p = tmp_path / "keys.yaml"
    p.write_text(yaml.dump({"keys": [{"key": KEY_A, "org_id": "x"}]}))
    auth = AuthConfig(
        enabled=True,
        keys=[KeyConfig(key=KEY_A, org_id="acme")],
        keys_file=str(p),
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_key_index(auth)


@pytest.mark.asyncio
async def test_reload_swaps_index(tmp_path: Path):
    p = tmp_path / "keys.yaml"
    p.write_text(yaml.dump({
        "keys": [
            {"key": KEY_A, "org_id": "acme"},
            {"key": KEY_OPS, "org_id": "ops", "ops_admin": True},
        ],
    }))
    cfg = MeridianConfig.from_dict({
        "auth": {"enabled": True, "keys_file": str(p), "keys": []},
        "backends": [],
    })
    await init_app(cfg, start_health=False)
    st = get_state()
    assert KEY_A in st.key_index
    assert KEY_B not in st.key_index

    p.write_text(yaml.dump({
        "keys": [
            {"key": KEY_B, "org_id": "globex"},
            {"key": KEY_OPS, "org_id": "ops", "ops_admin": True},
        ],
    }))
    n = reload_keys(st)
    assert n == 2
    assert KEY_B in st.key_index
    assert KEY_A not in st.key_index


@pytest.mark.asyncio
async def test_reload_endpoint_requires_ops_admin(tmp_path: Path):
    p = tmp_path / "keys.yaml"
    p.write_text(yaml.dump({
        "keys": [
            {"key": KEY_A, "org_id": "acme"},
            {"key": KEY_OPS, "org_id": "ops", "ops_admin": True},
        ],
    }))
    cfg = MeridianConfig.from_dict({
        "auth": {"enabled": True, "keys_file": str(p)},
        "backends": [],
    })
    await init_app(cfg, start_health=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        bare = await c.post("/meridian/reload")
        assert bare.status_code == 401
        denied = await c.post(
            "/meridian/reload",
            headers={"Authorization": f"Bearer {KEY_A}"},
        )
        assert denied.status_code == 403
        ok = await c.post(
            "/meridian/reload",
            headers={"Authorization": f"Bearer {KEY_OPS}"},
        )
        assert ok.status_code == 200
        assert ok.json()["reloaded"] is True
