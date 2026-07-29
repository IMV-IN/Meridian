"""Phase 3 Track 1: tenant isolation modes (shared | dedicated).

Dedicated mode guarantees: a pinned org is confined to its backend tag pool
(no cross-pool fallback, no cross-pool idle wake), and orgs without a pool
assignment are excluded from every reserved pool. Noisy-org traffic cannot
starve another org's capacity in either direction.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from meridian.api.routing import route, select_with_tier
from meridian.config.models import BackendConfig, MeridianConfig
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.affinity import SessionStore
from meridian.router.isolation import filter_visible, org_can_use, org_pool_tags
from meridian.router.strategies import RequestContext, create_strategy
from meridian.util.helpers import now_ms


def _backend(name: str, model: str = "m", tags: list[str] | None = None) -> Backend:
    return Backend(
        BackendConfig(name=name, url=f"http://{name}", model=model, tags=tags or [])
    )


def _state(backends: list[Backend], iso: dict) -> SimpleNamespace:
    cfg = MeridianConfig.from_dict(
        {
            "gateway": {"strategy": "least_inflight"},
            "isolation": iso,
        }
    )
    return SimpleNamespace(
        registry=BackendRegistry(backends),
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=None,
        canary=None,
    )


def _ctx() -> RequestContext:
    return RequestContext(prompt_tokens=10, max_tokens=10, cost=0.0)


# ── Config validation ──────────────────────────────────────────────────────


class TestIsolationConfig:
    def test_defaults_shared_no_pools(self) -> None:
        cfg = MeridianConfig()
        assert cfg.isolation.mode == "shared"
        assert cfg.isolation.pools == {}

    def test_bad_mode_rejected(self) -> None:
        with pytest.raises(Exception, match="isolation.mode"):
            MeridianConfig.from_dict({"isolation": {"mode": "partial"}})

    def test_dedicated_with_pools_loads(self) -> None:
        cfg = MeridianConfig.from_dict(
            {
                "isolation": {
                    "mode": "dedicated",
                    "pools": {"org-a": ["pool-a"], "org-b": ["pool-b", "spot"]},
                }
            }
        )
        assert cfg.isolation.mode == "dedicated"
        assert cfg.isolation.pools["org-b"] == ["pool-b", "spot"]

    # ── H3 regressions: malformed pools must fail fast, not silently open ──

    def test_empty_pool_list_rejected(self) -> None:
        """pools={org: []} used to turn that org into a superuser (empty set ⊆
        every backend's tags)."""
        with pytest.raises(Exception, match="empty"):
            MeridianConfig.from_dict(
                {"isolation": {"mode": "dedicated", "pools": {"org-a": []}}}
            )

    def test_blank_pool_key_rejected(self) -> None:
        with pytest.raises(Exception, match="non-empty"):
            MeridianConfig.from_dict(
                {"isolation": {"mode": "dedicated", "pools": {"": ["pool-a"]}}}
            )

    def test_blank_pool_tag_rejected(self) -> None:
        with pytest.raises(Exception, match="tags must be non-empty"):
            MeridianConfig.from_dict(
                {"isolation": {"mode": "dedicated", "pools": {"org-a": ["pool-a", "  "]}}}
            )

    def test_unknown_isolation_key_rejected(self) -> None:
        """A typo like ``pool:`` instead of ``pools:`` must not silently
        degrade dedicated isolation to 'dedicated with zero pools'."""
        with pytest.raises(Exception):
            MeridianConfig.from_dict(
                {"isolation": {"mode": "dedicated", "pool": {"org-a": ["pool-a"]}}}
            )


# ── Pure visibility logic ──────────────────────────────────────────────────


class TestVisibility:
    def _cfg(self) -> MeridianConfig:
        return MeridianConfig.from_dict(
            {
                "isolation": {
                    "mode": "dedicated",
                    "pools": {"org-a": ["pool-a"], "org-b": ["pool-b"]},
                }
            }
        )

    def test_shared_mode_everything_visible(self) -> None:
        cfg = MeridianConfig()
        b = _backend("x", tags=["pool-a"])
        assert org_can_use(cfg.isolation, "org-a", b) is True
        assert org_can_use(cfg.isolation, "rand", b) is True
        assert org_can_use(cfg.isolation, None, b) is True

    def test_pinned_org_sees_only_its_pool(self) -> None:
        iso = self._cfg().isolation
        in_pool = _backend("a1", tags=["pool-a", "fast"])
        out_pool = _backend("b1", tags=["pool-b"])
        poolless = _backend("shared", tags=[])
        assert org_can_use(iso, "org-a", in_pool) is True
        assert org_can_use(iso, "org-a", out_pool) is False
        assert org_can_use(iso, "org-a", poolless) is False

    def test_unlisted_org_excluded_from_reserved_pools(self) -> None:
        iso = self._cfg().isolation
        in_a = _backend("a1", tags=["pool-a"])
        in_b = _backend("b1", tags=["pool-b"])
        poolless = _backend("shared", tags=["fast"])
        assert org_can_use(iso, "rand", in_a) is False
        assert org_can_use(iso, "rand", in_b) is False
        assert org_can_use(iso, "rand", poolless) is True

    def test_pool_tags_are_subset_semantics(self) -> None:
        iso = MeridianConfig.from_dict(
            {"isolation": {"mode": "dedicated", "pools": {"org-a": ["pool-a", "fast"]}}}
        ).isolation
        exact = _backend("x", tags=["pool-a", "fast"])
        extra = _backend("y", tags=["pool-a", "fast", "spot"])
        partial = _backend("z", tags=["pool-a"])
        assert org_can_use(iso, "org-a", exact) is True
        assert org_can_use(iso, "org-a", extra) is True
        assert org_can_use(iso, "org-a", partial) is False

    def test_filter_visible_shared_is_passthrough(self) -> None:
        cfg = MeridianConfig()
        bs = [_backend("a", tags=["pool-a"]), _backend("b")]
        assert filter_visible(cfg.isolation, "org-a", bs) == bs

    def test_org_pool_tags_none_when_shared_or_unlisted(self) -> None:
        shared = MeridianConfig().isolation
        assert org_pool_tags(shared, "org-a") is None
        iso = self._cfg().isolation
        assert org_pool_tags(iso, "org-a") == {"pool-a"}
        assert org_pool_tags(iso, "rand") is None
        assert org_pool_tags(iso, None) is None


# ── Routing behavior ───────────────────────────────────────────────────────

POOLS = {"mode": "dedicated", "pools": {"org-a": ["pool-a"], "org-b": ["pool-b"]}}


class TestDedicatedRouting:
    def _pool_state(self) -> SimpleNamespace:
        return _state(
            [
                _backend("a1", tags=["pool-a"]),
                _backend("a2", tags=["pool-a"]),
                _backend("b1", tags=["pool-b"]),
                _backend("shared", tags=[]),
            ],
            POOLS,
        )

    def test_pinned_org_routes_only_to_own_pool(self) -> None:
        st = self._pool_state()
        for _ in range(20):
            b, _ = select_with_tier(st, "m", _ctx(), org_id="org-a")
            assert b is not None
            assert b.name in {"a1", "a2"}

    def test_pinned_org_empty_pool_isnt_rescued_by_neighbors(self) -> None:
        st = self._pool_state()
        st.registry.get("a1").healthy = False
        st.registry.get("a2").healthy = False
        selected, _ = select_with_tier(st, "m", _ctx(), org_id="org-a")
        # 503 upstream: no stealing from pool-b or shared, even when full.
        assert selected is None

    def test_unlisted_org_gets_unreserved_backends_only(self) -> None:
        st = self._pool_state()
        for _ in range(20):
            b, _ = select_with_tier(st, "m", _ctx(), org_id="rand")
            assert b is not None
            assert b.name == "shared"

    def test_anonymous_request_counts_as_unlisted(self) -> None:
        st = self._pool_state()
        b, _ = select_with_tier(st, "m", _ctx(), org_id=None)
        assert b is not None
        assert b.name == "shared"

    def test_idle_wake_confined_to_org_pool(self) -> None:
        st = _state(
            [
                _backend("a1", tags=["pool-a"]),
                _backend("b1", tags=["pool-b"]),
            ],
            POOLS,
        )
        st.registry.get("a1").idle_timeout_ms = 300_000.0
        st.registry.get("a1").touch(1_000_000.0)
        st.registry.get("a1").mark_idle_if_expired(1_000_000.0 + 400_000.0)
        st.registry.get("b1").healthy = False
        assert st.registry.get("a1").idle is True

        b, _ = select_with_tier(st, "m", _ctx(), org_id="org-a")
        assert b is not None
        assert b.name == "a1"
        assert b.idle is False

    def test_idle_neighbor_is_not_woken_for_foreign_org(self) -> None:
        st = _state(
            [
                _backend("b1", tags=["pool-b"]),
            ],
            POOLS,
        )
        b1 = st.registry.get("b1")
        b1.idle_timeout_ms = 300_000.0
        b1.touch(1_000_000.0)
        b1.mark_idle_if_expired(1_000_000.0 + 400_000.0)
        assert b1.idle is True

        selected, _ = select_with_tier(st, "m", _ctx(), org_id="org-a")
        assert selected is None
        assert b1.idle is True  # untouched by a foreign org's request

    def test_shared_mode_ignores_pool_config(self) -> None:
        st = _state(
            [
                _backend("b1", tags=["pool-b"]),
            ],
            {"mode": "shared", "pools": {"org-a": ["pool-a"]}},
        )
        b, _ = select_with_tier(st, "m", _ctx(), org_id="org-a")
        assert b is not None
        assert b.name == "b1"


# ── Tiering interplay ──────────────────────────────────────────────────────


class TestIsolationWithTiering:
    def _tiered_state(self) -> SimpleNamespace:
        cfg = MeridianConfig.from_dict(
            {
                "gateway": {"strategy": "least_inflight"},
                "isolation": POOLS,
                "tiering": {
                    "enabled": True,
                    "long_prompt_tokens": 100,
                    "long_decode_tokens": 100,
                    "tiers": {
                        "long_prompt": ["lp"],
                        "long_decode": ["ld"],
                        "default": ["gen"],
                    },
                },
            }
        )
        backends = [
            _backend("a-lp", tags=["pool-a", "lp"]),
            _backend("a-gen", tags=["pool-a", "gen"]),
            _backend("b-gen", tags=["pool-b", "gen"]),
            _backend("shared-gen", tags=["gen"]),
        ]
        return SimpleNamespace(
            registry=BackendRegistry(backends),
            strategy=create_strategy("least_inflight"),
            config=cfg,
            session_store=None,
            canary=None,
        )

    def test_pinned_org_tier_subpool_preferred(self) -> None:
        st = self._tiered_state()
        ctx = RequestContext(prompt_tokens=500, max_tokens=10, cost=0.0)
        b, tier = select_with_tier(st, "m", ctx, org_id="org-a")
        assert tier == "long_prompt"
        assert b is not None
        assert b.name == "a-lp"

    def test_tier_fallback_stays_inside_org_pool(self) -> None:
        st = self._tiered_state()
        st.registry.get("a-lp").healthy = False
        ctx = RequestContext(prompt_tokens=500, max_tokens=10, cost=0.0)
        b, _ = select_with_tier(st, "m", ctx, org_id="org-a")
        # long_prompt pool (pool-a ∩ lp) is down; falls back to org-a's whole
        # pool — never to b-gen's lp-less neighbor or shared-gen.
        assert b is not None
        assert b.name == "a-gen"

    def test_unlisted_org_tiering_excludes_reserved_pools(self) -> None:
        st = self._tiered_state()
        ctx = RequestContext(prompt_tokens=10, max_tokens=10, cost=0.0)
        b, _ = select_with_tier(st, "m", ctx, org_id="rand")
        assert b is not None
        assert b.name == "shared-gen"


# ── Session affinity interplay ─────────────────────────────────────────────


class TestAffinityUnderIsolation:
    def _affinity_state(self) -> SimpleNamespace:
        cfg = MeridianConfig.from_dict(
            {
                "gateway": {"strategy": "least_inflight"},
                "isolation": POOLS,
                "session_affinity": {"enabled": True, "ttl_s": 600},
            }
        )
        return SimpleNamespace(
            registry=BackendRegistry(
                [_backend("b1", tags=["pool-b"]), _backend("a1", tags=["pool-a"])]
            ),
            strategy=create_strategy("least_inflight"),
            config=cfg,
            session_store=SessionStore(ttl_ms=600_000, max_sessions=100, clock=now_ms),
            canary=None,
        )

    def test_pinned_out_of_pool_backend_is_remapped(self) -> None:
        from meridian.api.routing import _session_key

        st = self._affinity_state()
        # Session pinned while isolation was shared -> now org-a is pinned.
        st.session_store.put(_session_key("sess-1", "org-a"), "b1")
        backend, _, session_route = route(
            st, "m", _ctx(), session_id="sess-1", org_id="org-a"
        )
        assert backend is not None
        assert backend.name == "a1"
        assert session_route == "remapped"  # pool violation invalidated the pin
        # And the pin now points inside the org's pool.
        assert st.session_store.get(_session_key("sess-1", "org-a")) == "a1"

    def test_pinned_in_pool_backend_still_used(self) -> None:
        from meridian.api.routing import _session_key

        st = self._affinity_state()
        st.session_store.put(_session_key("sess-1", "org-a"), "a1")
        backend, _, session_route = route(
            st, "m", _ctx(), session_id="sess-1", org_id="org-a"
        )
        assert backend is not None
        assert backend.name == "a1"
        assert session_route == "pinned"

    def test_cross_org_same_session_id_cannot_read_or_overwrite_pin(self) -> None:
        """M8: raw session ids are client-supplied; without org-namespacing,
        org-b could read org-a's pin (then overwrite it on remap). The store
        seals each pin under its org namespace."""
        from meridian.api.routing import _session_key

        st = self._affinity_state()
        st.session_store.put(_session_key("sess-1", "org-a"), "a1")  # org-a's pin
        # org-b presents the identical session id — must NOT see org-a's pin.
        backend, _, session_route = route(
            st, "m", _ctx(), session_id="sess-1", org_id="org-b"
        )
        assert backend is not None
        assert backend.name == "b1"
        assert session_route == "new"  # fresh pin in org-b's namespace, not "pinned"
        # org-a's pin is untouched.
        assert st.session_store.get(_session_key("sess-1", "org-a")) == "a1"
        assert st.session_store.get(_session_key("sess-1", "org-b")) == "b1"
