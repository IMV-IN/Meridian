"""Phase 3 Track 3: key lifecycle API (GET/POST/DELETE /meridian/keys).

Manageability criteria: keys are administrable without config edits while
auth.keys_file stays the durable source of truth — every mutation is written
back atomically and hot-swapped into the index. The raw key is returned
exactly once (POST response); every other surface uses the non-secret id.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import yaml

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.auth.keys import (
    build_key_index,
    derive_key_id,
    generate_key,
    load_keys_from_file,
    save_keys_to_file,
)
from meridian.config.models import AuthConfig, KeyConfig, MeridianConfig

KEY_ADMIN = "mrdn_AdminKey000000000000000000aa"
KEY_VIEWER = "mrdn_ViewerKey0000000000000000aa"
KEY_ORG = "mrdn_OrgKey0000000000000000000aaa"

KEY_RE = re.compile(r"^mrdn_[A-Za-z0-9]{20,40}$")


# ── Pure helpers ───────────────────────────────────────────────────────────


class TestKeyIdAndGeneration:
    def test_derive_key_id_prefix(self) -> None:
        assert derive_key_id(KEY_ORG) == "mrdn_OrgKey00"

    def test_derive_key_id_stable_for_short_bodies(self) -> None:
        assert derive_key_id("mrdn_short") == "mrdn_short"

    def test_generate_key_matches_pattern(self) -> None:
        for _ in range(50):
            k = generate_key()
            assert KEY_RE.match(k), k

    def test_generated_keys_are_unique(self) -> None:
        assert len({generate_key() for _ in range(100)}) == 100

    def test_identity_carries_key_id(self) -> None:
        auth = AuthConfig(
            enabled=True, keys=[KeyConfig(key=KEY_ORG, org_id="acme")]
        )
        idx = build_key_index(auth)
        assert idx[KEY_ORG].key_id == "mrdn_OrgKey00"

    def test_explicit_key_id_wins(self) -> None:
        auth = AuthConfig(
            enabled=True,
            keys=[KeyConfig(key=KEY_ORG, org_id="acme", key_id="svc-embedder")],
        )
        idx = build_key_index(auth)
        assert idx[KEY_ORG].key_id == "svc-embedder"


class TestKeyFileWriter:
    def test_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "keys.yaml"
        save_keys_to_file(str(p), [KeyConfig(key=KEY_ORG, org_id="acme")])
        keys = load_keys_from_file(str(p))
        assert len(keys) == 1
        assert keys[0].key == KEY_ORG
        assert keys[0].org_id == "acme"

    def test_preserves_other_sections(self, tmp_path: Path) -> None:
        p = tmp_path / "keys.yaml"
        p.write_text(yaml.dump({"keys": [], "metadata": {"env": "prod"}}))
        save_keys_to_file(str(p), [KeyConfig(key=KEY_ORG, org_id="acme")])
        raw = yaml.safe_load(p.read_text())
        assert raw["metadata"] == {"env": "prod"}
        assert len(raw["keys"]) == 1

    def test_overwrite_updates_in_place(self, tmp_path: Path) -> None:
        p = tmp_path / "keys.yaml"
        save_keys_to_file(str(p), [KeyConfig(key=KEY_ORG, org_id="acme")])
        save_keys_to_file(str(p), [KeyConfig(key=KEY_VIEWER, org_id="globex")])
        keys = load_keys_from_file(str(p))
        assert {k.key for k in keys} == {KEY_VIEWER}

    def test_no_temp_file_left(self, tmp_path: Path) -> None:
        p = tmp_path / "keys.yaml"
        save_keys_to_file(str(p), [KeyConfig(key=KEY_ORG, org_id="acme")])
        assert not (tmp_path / "keys.yaml.tmp").exists()


# ── HTTP surface ───────────────────────────────────────────────────────────


def _cfg(tmp_path: Path, *, keys_file: bool = True, inline: bool = False) -> MeridianConfig:
    inline_keys = [{"key": KEY_ORG, "org_id": "acme"}] if inline else []
    if keys_file:
        p = tmp_path / "keys.yaml"
        p.write_text(
            yaml.dump(
                {
                    "keys": [
                        {"key": KEY_ADMIN, "org_id": "ops", "ops_admin": True},
                        {"key": KEY_VIEWER, "org_id": "acme", "role": "viewer"},
                    ]
                }
            )
        )
        auth: dict = {"enabled": True, "keys_file": str(p), "keys": inline_keys}
    else:
        auth = {"enabled": True, "keys": [{"key": KEY_ADMIN, "org_id": "ops", "ops_admin": True}]}
    return MeridianConfig.from_dict({"auth": auth, "backends": []})


async def _client_for(cfg: MeridianConfig) -> httpx.AsyncClient:
    await init_app(cfg, start_health=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    )


class TestKeysListEndpoint:
    async def test_requires_auth_and_admin(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            bare = await c.get("/meridian/keys")
            assert bare.status_code == 401
            denied = await c.get(
                "/meridian/keys", headers={"Authorization": f"Bearer {KEY_VIEWER}"}
            )
            assert denied.status_code == 403
            ok = await c.get(
                "/meridian/keys", headers={"Authorization": f"Bearer {KEY_ADMIN}"}
            )
            assert ok.status_code == 200

    async def test_listing_is_redacted_and_sourced(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path, inline=True)) as c:
            r = await c.get(
                "/meridian/keys", headers={"Authorization": f"Bearer {KEY_ADMIN}"}
            )
            keys = r.json()["keys"]
            assert len(keys) == 3  # admin + viewer (file) + org (inline)
            srcs = {k["key_id"]: k["source"] for k in keys}
            assert srcs["mrdn_OrgKey00"] == "inline"
            assert srcs["mrdn_AdminKey"] == str(tmp_path / "keys.yaml")
            # No raw key material anywhere in the payload.
            assert KEY_ADMIN not in r.text
            assert KEY_VIEWER not in r.text
            assert KEY_ORG not in r.text


class TestKeysCreateEndpoint:
    async def test_create_generated_key_full_onetime_shown(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "globex", "role": "viewer"},
            )
            assert r.status_code == 201
            payload = r.json()
            assert KEY_RE.match(payload["key"])
            assert payload["org_id"] == "globex"
            assert payload["key_id"] == derive_key_id(payload["key"])

            # Durable: file actually changed.
            on_disk = load_keys_from_file(str(tmp_path / "keys.yaml"))
            assert any(k.org_id == "globex" for k in on_disk)

            # Hot: the new key authenticates immediately.
            st = get_state()
            assert payload["key"] in st.key_index

            # Opaque afterward: the listing never shows the raw value.
            listing = await c.get(
                "/meridian/keys", headers={"Authorization": f"Bearer {KEY_ADMIN}"}
            )
            assert payload["key"] not in listing.text

    async def test_create_explicit_key(self, tmp_path: Path) -> None:
        explicit = "mrdn_ExplicitKey0000000000000000ab"
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "acme", "key": explicit},
            )
            assert r.status_code == 201
            assert r.json()["key"] == explicit

    async def test_create_duplicate_key_conflicts(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "acme", "key": KEY_VIEWER},
            )
            assert r.status_code == 409

    async def test_create_duplicate_key_id_conflicts(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "acme", "key_id": "mrdn_AdminKey"},
            )
            assert r.status_code == 409

    async def test_create_bad_explicit_key_is_400(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "acme", "key": "not-a-key"},
            )
            assert r.status_code == 400

    async def test_create_requires_keys_file(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path, keys_file=False)) as c:
            r = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "acme"},
            )
            assert r.status_code == 400


class TestKeysDeleteEndpoint:
    async def test_delete_removes_key_and_index(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            victim = await c.post(
                "/meridian/keys",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                json={"org_id": "doomed"},
            )
            full_key = victim.json()["key"]
            key_id = victim.json()["key_id"]

            r = await c.delete(
                f"/meridian/keys/{key_id}",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
            )
            assert r.status_code == 200
            assert r.json()["deleted"] is True
            assert r.json()["org_id"] == "doomed"

            st = get_state()
            assert full_key not in st.key_index
            on_disk = load_keys_from_file(str(tmp_path / "keys.yaml"))
            assert all(k.org_id != "doomed" for k in on_disk)

    async def test_delete_unknown_is_404(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.delete(
                "/meridian/keys/mrdn_nosuchid",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
            )
            assert r.status_code == 404

    async def test_delete_inline_key_refused(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path, inline=True)) as c:
            r = await c.delete(
                f"/meridian/keys/{derive_key_id(KEY_ORG)}",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
            )
            assert r.status_code == 400
            # Not actually deleted.
            st = get_state()
            assert KEY_ORG in st.key_index

    async def test_delete_requires_admin(self, tmp_path: Path) -> None:
        async with await _client_for(_cfg(tmp_path)) as c:
            r = await c.delete(
                f"/meridian/keys/{derive_key_id(KEY_VIEWER)}",
                headers={"Authorization": f"Bearer {KEY_VIEWER}"},
            )
            assert r.status_code == 403
