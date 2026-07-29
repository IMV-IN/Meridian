"""Phase 3 Track 4: per-key usage tracking.

Budget meter keys gain a ``key`` scope (caps per API key id), and the cost
ledger records the presenting key's non-secret id so /meridian/usage can
filter by ``?key=`` and the CSV export carries a key_id column.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.auth.models import IdentityContext
from meridian.config.models import BudgetConfig, MeridianConfig
from meridian.cost.ledger import InMemoryCostLedger, SqliteCostLedger
from meridian.cost.record import record_actual_usage
from meridian.usage.keys import build_meter_keys

KEY_ACME = "mrdn_KeyAcme000000000000000000aa"
KEY_GLOBEX = "mrdn_KeyGlobex000000000000000aa"
KEY_ACME_B = "mrdn_KeyAcmeB0000000000000000ab"
KEY_ADMIN = "mrdn_KeyAdmin00000000000000000ab"


# ── Budget meter: key scope ────────────────────────────────────────────────


class TestKeyBudgetScope:
    def test_key_scope_keys_built(self) -> None:
        budgets = BudgetConfig(
            enabled=True,
            keys={"svc-key": {"daily": {"tokens": 100, "requests": 10}}},
        )
        idn = IdentityContext(org_id="acme", key_id="svc-key")
        keys = build_meter_keys(idn, budgets)
        assert {(k.scope_level, k.scope_id) for k in keys} == {("key", "svc-key")}
        assert {k.metric for k in keys} == {"tokens", "requests"}

    def test_no_key_scope_when_unconfigured_or_missing_id(self) -> None:
        budgets = BudgetConfig(
            enabled=True, keys={"other": {"daily": {"tokens": 5}}}
        )
        assert build_meter_keys(
            IdentityContext(org_id="acme", key_id="svc-key"), budgets
        ) == []
        assert build_meter_keys(IdentityContext(org_id="acme"), budgets) == []

    def test_key_scope_cascades_with_org(self) -> None:
        budgets = BudgetConfig(
            enabled=True,
            keys={"svc-key": {"daily": {"tokens": 100}}},
            orgs={"acme": {"daily": {"tokens": 1000}}},
        )
        idn = IdentityContext(org_id="acme", key_id="svc-key")
        levels = {k.scope_level for k in build_meter_keys(idn, budgets)}
        assert levels == {"key", "org"}


# ── Ledger key dimension ───────────────────────────────────────────────────


class TestLedgerKeyDimension:
    def test_inmemory_splits_by_key(self) -> None:
        led = InMemoryCostLedger()
        led.record(org_id="acme", team_id="", model="m",
                   prompt_tokens=5, completion_tokens=1, cost=0.1, key_id="k1")
        led.record(org_id="acme", team_id="", model="m",
                   prompt_tokens=7, completion_tokens=3, cost=0.2, key_id="k2")
        rows = led.query(org_id="acme")
        assert len(rows) == 2  # same org/model/day but different keys
        assert {r.key_id for r in rows} == {"k1", "k2"}

        k1 = led.query(org_id="acme", key_id="k1")
        assert len(k1) == 1
        assert k1[0].prompt_tokens == 5

    def test_key_less_rows_default_empty(self) -> None:
        led = InMemoryCostLedger()
        led.record(org_id="acme", team_id="", model="m",
                   prompt_tokens=1, completion_tokens=1, cost=0.0)
        rows = led.query()
        assert rows[0].key_id == ""

    def test_sqlite_splits_and_filters(self, tmp_path: Path) -> None:
        led = SqliteCostLedger(str(tmp_path / "cost.db"))
        led.record(org_id="acme", team_id="", model="m",
                   prompt_tokens=5, completion_tokens=1, cost=0.1, key_id="k1")
        led.record(org_id="acme", team_id="", model="m",
                   prompt_tokens=7, completion_tokens=1, cost=0.2, key_id="k2")
        assert len(led.query(org_id="acme")) == 2
        k2 = led.query(key_id="k2")
        assert len(k2) == 1
        assert k2[0].prompt_tokens == 7

    def test_sqlite_migration_from_v1_schema(self, tmp_path: Path) -> None:
        """Pre-0.12 DBs (no key_id column) are rebuilt, rows carried with ''."""
        db = tmp_path / "cost.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            """
            CREATE TABLE cost_ledger (
                org_id TEXT NOT NULL, team_id TEXT NOT NULL,
                model TEXT NOT NULL, day TEXT NOT NULL,
                prompt_tokens REAL NOT NULL DEFAULT 0,
                completion_tokens REAL NOT NULL DEFAULT 0,
                requests REAL NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (org_id, team_id, model, day)
            )
            """
        )
        conn.execute(
            "INSERT INTO cost_ledger VALUES ('acme', '', 'm', '2026-07-01', 100, 50, 3, 0.5)"
        )
        conn.commit()
        conn.close()

        led = SqliteCostLedger(str(db))  # triggers migration
        rows = led.query(org_id="acme", window_days=10_000)
        assert len(rows) == 1
        assert rows[0].key_id == ""
        assert rows[0].prompt_tokens == 100

        # New rows with a key_id coexist with the migrated one.
        led.record(org_id="acme", team_id="", model="m", key_id="k1",
                   prompt_tokens=1, completion_tokens=1, cost=0.01)
        all_rows = led.query(org_id="acme", window_days=10_000)
        assert len(all_rows) == 2


# ── /meridian/usage?key= — end to end ──────────────────────────────────────


def _auth_cfg() -> MeridianConfig:
    return MeridianConfig.from_dict({
        "auth": {
            "enabled": True,
            "keys": [
                {"key": KEY_ACME, "org_id": "acme"},
                {"key": KEY_ACME_B, "org_id": "acme", "key_id": "svc-b"},
                {"key": KEY_GLOBEX, "org_id": "globex"},
                {"key": KEY_ADMIN, "org_id": "ops", "cost_admin": True},
            ],
        },
        "cost": {"enabled": True, "store": "memory"},
        "backends": [],
    })


class TestUsageKeyFilter:
    async def test_key_filter_and_column(self) -> None:
        await init_app(_auth_cfg(), start_health=False)
        st = get_state()
        # acme traffic on two different keys (key_id auto = prefix for KEY_ACME)
        record_actual_usage(st, model="demo", org_id="acme", team_id="",
                            prompt_tokens=10, completion_tokens=2, key_id="mrdn_KeyAcme0")
        record_actual_usage(st, model="demo", org_id="acme", team_id="",
                            prompt_tokens=30, completion_tokens=6, key_id="svc-b")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/meridian/usage",
                headers={"Authorization": f"Bearer {KEY_ACME}"},
            )
            assert r.status_code == 200
            rows = r.json()["rows"]
            assert {row["key_id"] for row in rows} == {"mrdn_KeyAcme0", "svc-b"}

            filtered = await c.get(
                "/meridian/usage",
                headers={"Authorization": f"Bearer {KEY_ACME}"},
                params={"key": "svc-b"},
            )
            frows = filtered.json()["rows"]
            assert len(frows) == 1
            assert frows[0]["key_id"] == "svc-b"
            assert frows[0]["prompt_tokens"] == 30

    async def test_key_filter_respects_org_clamp(self) -> None:
        await init_app(_auth_cfg(), start_health=False)
        st = get_state()
        record_actual_usage(st, model="demo", org_id="globex", team_id="",
                            prompt_tokens=99, completion_tokens=9, key_id="svc-b")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
        ) as c:
            # acme key filtering by a key_id that exists only in globex sees
            # nothing — the org clamp still holds.
            r = await c.get(
                "/meridian/usage",
                headers={"Authorization": f"Bearer {KEY_ACME}"},
                params={"key": "svc-b"},
            )
            assert r.status_code == 200
            assert r.json()["rows"] == []

            # cost admin crosses.
            r2 = await c.get(
                "/meridian/usage",
                headers={"Authorization": f"Bearer {KEY_ADMIN}"},
                params={"key": "svc-b"},
            )
            assert len(r2.json()["rows"]) == 1

    async def test_csv_has_key_column(self) -> None:
        await init_app(_auth_cfg(), start_health=False)
        st = get_state()
        record_actual_usage(st, model="demo", org_id="acme", team_id="",
                            prompt_tokens=10, completion_tokens=2, key_id="svc-b")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/meridian/usage.csv",
                headers={"Authorization": f"Bearer {KEY_ACME}"},
            )
            assert r.status_code == 200
            lines = r.text.strip().splitlines()
            assert lines[0].split(",")[:5] == [
                "org_id", "team_id", "model", "day", "key_id"
            ]
            assert "svc-b" in lines[1]

            # And a per-key CSV export is a one-cell filter away.
            rf = await c.get(
                "/meridian/usage.csv",
                headers={"Authorization": f"Bearer {KEY_ACME}"},
                params={"key": "svc-b"},
            )
            assert len(rf.text.strip().splitlines()) == 2  # header + 1 row
