"""Phase 3 Track 2: canary rollout controller and wiring.

Controller: weight-split, scheduled step advance, error-based rollback.
Routing integration: pool split, spillover, dedicated-mode bypass.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from meridian.api.routing import select_with_tier
from meridian.config.models import BackendConfig, CanaryConfig, MeridianConfig
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.canary import CanaryController
from meridian.router.strategies import RequestContext, create_strategy


def _backend(name: str, model: str = "m", tags: list[str] | None = None) -> Backend:
    return Backend(
        BackendConfig(name=name, url=f"http://{name}", model=model, tags=tags or [])
    )


def _state(cfg: MeridianConfig, canary: CanaryController | None) -> SimpleNamespace:
    registry = BackendRegistry(
        [_backend("b1", tags=["stable"]), _backend("b2", tags=["canary", "gpu"])]
    )
    return SimpleNamespace(
        registry=registry,
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=None,
        canary=canary,
    )


def _ctx() -> RequestContext:
    return RequestContext(prompt_tokens=10, max_tokens=10, cost=0.0)


# ── Controller unit tests ──────────────────────────────────────────────


def test_pick_pool_splits_by_weight() -> None:
    fake_clock = iter([100.0])
    cfg = CanaryConfig(
        enabled=True,
        start_weight=30.0,
        canary_tags=["canary"],
        stable_tags=["stable"],
        rollback_min_samples=999,  # never roll back in this test
    )
    ctrl = CanaryController(cfg, clock=lambda: next(fake_clock))
    # roll=10 → canary (10 < 30). roll=50 → stable (50 >= 30).
    assert ctrl.pick_pool(10.0) == "canary"
    assert ctrl.pick_pool(50.0) == "stable"


def test_pick_pool_zero_weight() -> None:
    cfg = CanaryConfig(
        enabled=True, start_weight=0.0, rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    assert ctrl.pick_pool(0.0) == "stable"
    assert ctrl.pick_pool(99.9) == "stable"


def test_pick_pool_full_weight() -> None:
    cfg = CanaryConfig(
        enabled=True, start_weight=100.0, rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    assert ctrl.pick_pool(99.9) == "canary"
    assert ctrl.pick_pool(100.0) == "stable"


def test_record_backend_ignores_non_canary() -> None:
    cfg = CanaryConfig(
        enabled=True,
        canary_tags=["canary"],
        stable_tags=["stable"],
        rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    b_stable = _backend("b1", tags=["stable"])
    ctrl.record_backend(b_stable, 200)
    assert len(ctrl._window) == 0


def test_record_backend_tracks_canary_backends() -> None:
    cfg = CanaryConfig(
        enabled=True,
        canary_tags=["canary"],
        stable_tags=["stable"],
        rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    b_canary = _backend("b2", tags=["canary", "gpu"])
    ctrl.record_backend(b_canary, 200)
    ctrl.record_backend(b_canary, 500)
    assert len(ctrl._window) == 2
    assert ctrl._window[0][1] is True
    assert ctrl._window[1][1] is False


def test_tick_advances_through_steps() -> None:
    times = [100.0, 110.0, 125.0, 140.0, 150.0]
    it = iter(times)
    cfg = CanaryConfig(
        enabled=True,
        start_weight=0.0,
        canary_tags=["canary"],
        stable_tags=["stable"],
        steps=[
            {"weight": 10.0, "duration_s": 10.0},
            {"weight": 50.0, "duration_s": 10.0},
            {"weight": 100.0, "duration_s": None},
        ],
        rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg, clock=lambda: next(it))
    # Tick 0 (t=110): step 0 started → weight 10%.
    ctrl.tick()
    assert ctrl.weight == 10.0
    assert ctrl._done is False
    # Tick 1 (t=125): step 0 expired (15 >= 10) → step 1 → weight 50%.
    ctrl.tick()
    assert ctrl.weight == 50.0
    assert ctrl._done is False
    # Tick 2 (t=140): step 1 expired (15 >= 10) → step 2 (no duration) → weight 100%, done.
    ctrl.tick()
    assert ctrl.weight == 100.0
    assert ctrl._done is True


def test_tick_empty_schedule_done_immediately() -> None:
    cfg = CanaryConfig(
        enabled=True,
        start_weight=50.0,
        steps=[],
        rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    assert ctrl._done is True
    ctrl.tick()
    assert ctrl.weight == 50.0


def test_tick_rolls_back_on_error_rate() -> None:
    times = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    it = iter(times)
    cfg = CanaryConfig(
        enabled=True,
        start_weight=50.0,
        rollback_min_samples=3,
        rollback_error_rate=0.3,
        window_s=60.0,
    )
    ctrl = CanaryController(cfg, clock=lambda: next(it))
    b_canary = _backend("b2", tags=["canary", "gpu"])
    # 2 errors out of 4 samples = 50% > 30% → rollback.
    ctrl.record_backend(b_canary, 200)
    ctrl.record_backend(b_canary, 200)
    ctrl.record_backend(b_canary, 500)
    ctrl.record_backend(b_canary, 500)
    ctrl.tick()
    assert ctrl.weight == 0.0
    assert ctrl._rolled_back is True


def test_tick_no_rollback_below_min_samples() -> None:
    cfg = CanaryConfig(
        enabled=True,
        start_weight=50.0,
        rollback_min_samples=10,
        rollback_error_rate=0.01,
        window_s=60.0,
    )
    ctrl = CanaryController(cfg)
    b_canary = _backend("b2", tags=["canary", "gpu"])
    # 2 errors out of 2 samples but min_samples=10 → no rollback.
    ctrl.record_backend(b_canary, 500)
    ctrl.record_backend(b_canary, 500)
    ctrl.tick()
    assert ctrl.weight == 50.0
    assert ctrl._rolled_back is False


def test_tick_prunes_stale_window() -> None:
    """Old samples outside window_s are dropped before error-rate check."""
    times = [0.0, 0.0, 100.0]
    it = iter(times)
    cfg = CanaryConfig(
        enabled=True,
        start_weight=50.0,
        rollback_min_samples=1,
        rollback_error_rate=0.01,
        window_s=50.0,
    )
    ctrl = CanaryController(cfg, clock=lambda: next(it))
    b_canary = _backend("b2", tags=["canary", "gpu"])
    ctrl.record_backend(b_canary, 500)  # at t=0
    # After pruning at t=100, the sample at t=0 is gone → window empty → no rollback.
    ctrl.tick()
    assert ctrl.weight == 50.0


def test_status_returns_expected_fields() -> None:
    cfg = CanaryConfig(
        enabled=True,
        start_weight=25.0,
        canary_tags=["canary"],
        stable_tags=["stable"],
        steps=[{"weight": 100.0, "duration_s": 10.0}],
    )
    ctrl = CanaryController(cfg)
    s = ctrl.status()
    assert s["enabled"] is True
    assert s["weight"] == 25.0
    assert s["step"] == -1
    assert s["rolled_back"] is False
    assert s["schedule_done"] is False
    assert s["canary_tags"] == ["canary"]
    assert s["stable_tags"] == ["stable"]
    assert s["window_samples"] == 0
    assert s["window_error_rate"] is None


def test_pick_pool_no_tags() -> None:
    """Default tags should be used."""
    cfg = CanaryConfig(
        enabled=True,
        start_weight=50.0,
        rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    assert ctrl.pick_pool(0.0) == "canary"
    assert ctrl.pick_pool(50.0) == "stable"


# ── Routing integration ────────────────────────────────────────────────


def test_canary_select_splits_traffic() -> None:
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "canary": {
            "enabled": True,
            "canary_tags": ["canary"],
            "stable_tags": ["stable"],
            "start_weight": 100.0,
            "rollback_min_samples": 999,
        },
    })
    ctrl = CanaryController(cfg.canary)
    st = _state(cfg, ctrl)
    # At weight=100%, _canary_select should always pick the canary pool side.
    # b2 has tags ["canary", "gpu"], b1 has ["stable"].
    backend, tier = select_with_tier(st, "m", _ctx())
    assert backend is not None
    assert "canary" in backend.tags


def test_canary_select_spills_to_stable_when_canary_empty() -> None:
    """When the canary pool has no eligible backend, spill to stable."""
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "canary": {
            "enabled": True,
            "canary_tags": ["canary"],
            "stable_tags": ["stable"],
            "start_weight": 100.0,
            "rollback_min_samples": 999,
        },
    })
    ctrl = CanaryController(cfg.canary)
    # Only stable backends exist (no "canary" tag on any backend).
    registry = BackendRegistry([_backend("b1", tags=["stable"])])
    st = SimpleNamespace(
        registry=registry,
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=None,
        canary=ctrl,
    )
    backend, tier = select_with_tier(st, "m", _ctx())
    assert backend is not None
    assert "stable" in backend.tags


def test_canary_disabled_no_controller() -> None:
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "canary": {"enabled": False},
    })
    st = _state(cfg, None)
    backend, tier = select_with_tier(st, "m", _ctx())
    assert backend is not None


def test_dedicated_mode_bypasses_canary() -> None:
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "isolation": {"mode": "dedicated", "pools": {"org_a": ["dedicated-pool"]}},
        "canary": {
            "enabled": True,
            "canary_tags": ["canary"],
            "stable_tags": ["stable"],
            "start_weight": 100.0,
            "rollback_min_samples": 999,
        },
    })
    ctrl = CanaryController(cfg.canary)
    # Only the dedicated-pool backend for the pinned org.
    registry = BackendRegistry([
        _backend("dedicated_b", tags=["dedicated-pool"]),
        _backend("canary_b", tags=["canary"]),
    ])
    st = SimpleNamespace(
        registry=registry,
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=None,
        canary=ctrl,
    )
    backend, tier = select_with_tier(st, "m", _ctx(), org_id="org_a")
    assert backend is not None
    assert "dedicated-pool" in backend.tags
    assert "canary" not in backend.tags


def test_status_endpoint_includes_canary() -> None:
    """/meridian/status includes canary block when canary is active."""
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "canary": {
            "enabled": True,
            "start_weight": 10.0,
            "rollback_min_samples": 999,
        },
    })
    ctrl = CanaryController(cfg.canary)
    st = SimpleNamespace(
        registry=BackendRegistry([_backend("b1", tags=["stable"])]),
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=None,
        canary=ctrl,
    )
    from meridian.api.main import _status_body
    body = _status_body(st)
    assert "canary" in body
    assert body["canary"]["weight"] == 10.0


def test_finalize_records_canary_backend() -> None:
    """finalize_request calls canary.record_backend for canary-pool backends."""
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "canary": {
            "enabled": True,
            "rollback_min_samples": 999,
        },
    })
    ctrl = CanaryController(cfg.canary)
    st = SimpleNamespace(
        registry=BackendRegistry([_backend("b1", tags=["stable"])]),
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=None,
        canary=ctrl,
        request_logger=_FakeLogger(),
        audit_publisher=_FakePublisher(),
        recent_requests=[],
        record_request=lambda *a, **kw: None,
    )
    from meridian.api.finalize import finalize_request
    b = _backend("b1", tags=["stable"])
    finalize_request(
        state=st,
        request_id="r1",
        model="m",
        stream=False,
        backend=b,
        status_code=200,
        start=1000.0,
        error_type=None,
        request_ctx=_ctx(),
        tier_name=None,
        session_route=None,
        org_id=None,
        team_id=None,
    )
    # record_backend should have been called (even for stable, it's a no-op
    # since canary_tags defaults to ["canary"] which b1 doesn't have).
    assert len(ctrl._window) == 0


def test_canary_weight_metric_set_on_tick() -> None:
    from meridian.metrics.collectors import CANARY_WEIGHT
    cfg = CanaryConfig(
        enabled=True,
        start_weight=50.0,
        rollback_min_samples=999,
    )
    ctrl = CanaryController(cfg)
    ctrl.tick()
    assert CANARY_WEIGHT._value.get() == 50.0

    ctrl._weight = 75.0
    ctrl.tick()
    assert CANARY_WEIGHT._value.get() == 75.0


class _FakeLogger:
    def log(self, **kwargs: object) -> None:
        pass

    def close(self) -> None:
        pass


class _FakePublisher:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def enqueue(self, event: object) -> None:
        pass


# ── Review regressions (H1, H2, M5, M7, config validation) ─────────────────


class TestCanaryConfigValidation:
    def test_empty_canary_tags_rejected_when_enabled(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MeridianConfig.from_dict({
                "canary": {"enabled": True, "canary_tags": []},
            })

    def test_overlapping_pools_rejected_when_enabled(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="overlap"):
            MeridianConfig.from_dict({
                "canary": {
                    "enabled": True,
                    "canary_tags": ["gpu", "canary"],
                    "stable_tags": ["gpu", "stable"],
                },
            })

    def test_same_config_ok_when_disabled(self) -> None:
        """Disabled canary defers validation — an operator may stage config."""
        cfg = MeridianConfig.from_dict({
            "canary": {"enabled": False, "canary_tags": [], "stable_tags": []},
        })
        assert cfg.canary.enabled is False

    def test_unknown_canary_key_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MeridianConfig.from_dict({
                "canary": {"enabled": True, "wieght": 50.0},  # typo → forbid
            })


class TestFallbackWhenPoolsEmpty:
    """H1: enabling canary on a deployment whose backends carry no
    canary/stable tags must degrade to default routing, not 503 everything."""

    def _state_untagged(self) -> SimpleNamespace:
        cfg = MeridianConfig.from_dict({
            "gateway": {"strategy": "least_inflight"},
            "canary": {
                "enabled": True,
                "canary_tags": ["canary"],
                "stable_tags": ["stable"],
                "start_weight": 100.0,
                "rollback_min_samples": 999,
            },
        })
        backends = [_backend("b1", tags=["gpu"]), _backend("b2", tags=["small"])]
        return SimpleNamespace(
            registry=BackendRegistry(backends),
            strategy=create_strategy("least_inflight"),
            config=cfg,
            session_store=None,
            canary=CanaryController(cfg.canary),
        )

    def test_untagged_deployment_falls_back(self) -> None:
        st = self._state_untagged()
        backend, tier = select_with_tier(st, "m", _ctx())
        assert backend is not None  # was None before the fix (total 503)

    def test_fallback_does_not_defeat_isolation(self) -> None:
        st = self._state_untagged()
        st.config = MeridianConfig.from_dict({
            "gateway": {"strategy": "least_inflight"},
            "isolation": {"mode": "dedicated", "pools": {"vip": ["gpu"]}},
            "canary": {
                "enabled": True,
                "canary_tags": ["canary"],
                "stable_tags": ["stable"],
                "start_weight": 100.0,
                "rollback_min_samples": 999,
            },
        })
        # Unlisted org: gpu backend is reserved — fallback must still exclude it.
        backend, _ = select_with_tier(st, "m", _ctx(), org_id="other")
        assert backend is not None
        assert backend.name == "b2"


class TestPinnedSessionAcrossRollback:
    """H2: an affinity pin onto a canary-pool backend must not outlive the
    auto-rollback — that would indefinitely serve a pool Meridian just
    declared bad."""

    def _state(self) -> SimpleNamespace:
        cfg = MeridianConfig.from_dict({
            "gateway": {"strategy": "least_inflight"},
            "canary": {
                "enabled": True,
                "canary_tags": ["canary"],
                "stable_tags": ["stable"],
                "start_weight": 100.0,
                "rollback_min_samples": 2,
                "rollback_error_rate": 0.3,
            },
            "session_affinity": {"enabled": True},
        })
        c = _backend("cb", tags=["canary"])
        s = _backend("sb", tags=["stable"])
        from meridian.router.affinity import SessionStore
        from meridian.util.helpers import now_ms

        return SimpleNamespace(
            registry=BackendRegistry([c, s]),
            strategy=create_strategy("least_inflight"),
            config=cfg,
            session_store=SessionStore(ttl_ms=600_000, max_sessions=100, clock=now_ms),
            canary=CanaryController(cfg.canary),
        )

    def test_pin_remaps_after_rollback(self) -> None:
        from meridian.api.routing import _session_key, route

        st = self._state()
        b, _, r1 = route(st, "m", _ctx(), session_id="s1")
        assert b is not None and "canary" in b.tags and r1 == "new"

        # Canary pool goes bad → controller rolls the weight back to 0.
        cb = st.registry.get("cb")
        assert cb is not None
        st.canary.record_backend(cb, 500)
        st.canary.record_backend(cb, 500)
        st.canary.tick()
        assert st.canary.weight == 0.0 and st.canary.rolled_back

        # The pinned session must remap onto the stable pool, not keep
        # serving the rolled-back backend via the pin.
        b2, _, r2 = route(st, "m", _ctx(), session_id="s1")
        assert b2 is not None
        assert "stable" in b2.tags
        assert r2 == "remapped"
        assert st.session_store.get(_session_key("s1", None)) == "sb"

    def test_pin_survives_weight_changes_when_not_rolled_back(self) -> None:
        """Weight tuning alone must not churn existing pins (locality is a
        feature); only a rollback invalidates them."""
        from meridian.api.routing import route

        st = self._state()
        b, _, _ = route(st, "m", _ctx(), session_id="s1")
        assert b is not None and "canary" in b.tags
        st.canary._weight = 10.0  # demote WITHOUT declaring the pool bad
        b2, _, r2 = route(st, "m", _ctx(), session_id="s1")
        assert b2 is not None and "canary" in b2.tags and r2 == "pinned"


class TestReloadRearmPrevention:
    """M5: rebuilding the controller on any canary-section edit must not
    silently re-arm a pool that auto-rolled back."""

    def test_merge_carries_rollback_when_tags_match(self) -> None:
        old_cfg = CanaryConfig(
            enabled=True, start_weight=50.0, rollback_min_samples=2,
            rollback_error_rate=0.3,
        )
        old = CanaryController(old_cfg)
        b = _backend("cb", tags=["canary"])
        old.record_backend(b, 500)
        old.record_backend(b, 500)
        old.tick()
        assert old.rolled_back

        # Operator edits tick_s — tags unchanged.
        new_cfg = old_cfg.model_copy(update={"tick_s": 1.0})
        new = CanaryController(new_cfg)
        new.merge_from(old)
        assert new.rolled_back is True
        assert new.weight == 0.0

    def test_merge_ignored_when_tags_change(self) -> None:
        old_cfg = CanaryConfig(
            enabled=True, start_weight=50.0, rollback_min_samples=2,
            rollback_error_rate=0.3,
        )
        old = CanaryController(old_cfg)
        b = _backend("cb", tags=["canary"])
        old.record_backend(b, 500)
        old.record_backend(b, 500)
        old.tick()
        assert old.rolled_back

        new_cfg = old_cfg.model_copy(update={"canary_tags": ["canary-v2"]})
        new = CanaryController(new_cfg)
        new.merge_from(old)
        assert new.rolled_back is False  # retagged rollout = clean slate
        assert new.weight == 50.0


class TestWeightGaugeLifecycle:
    """M7: the gauge reflects the split from construction, and clears when
    the controller stops (a disabled canary must not export a stale weight)."""

    def test_gauge_set_on_init(self) -> None:
        from meridian.metrics.collectors import CANARY_WEIGHT

        cfg = CanaryConfig(enabled=True, start_weight=42.0, rollback_min_samples=999)
        CanaryController(cfg)  # no start, no tick needed
        assert CANARY_WEIGHT._value.get() == 42.0

    async def test_gauge_cleared_on_stop(self) -> None:
        from meridian.metrics.collectors import CANARY_WEIGHT

        cfg = CanaryConfig(
            enabled=True, start_weight=42.0, rollback_min_samples=999, tick_s=60.0,
        )
        ctrl = CanaryController(cfg)
        await ctrl.start()
        await ctrl.stop()
        assert CANARY_WEIGHT._value.get() == 0.0


class TestPositiveRecordBackend:
    """finalize_request feeds canary-pool outcomes into the rollback window
    (the earlier test only covered the no-op stable path)."""

    def test_finalize_records_canary_pool_outcome(self) -> None:
        cfg = MeridianConfig.from_dict({
            "gateway": {"strategy": "least_inflight"},
            "canary": {"enabled": True, "rollback_min_samples": 999},
        })
        ctrl = CanaryController(cfg.canary)
        st = SimpleNamespace(
            registry=BackendRegistry([_backend("b1", tags=["canary"])]),
            strategy=create_strategy("least_inflight"),
            config=cfg,
            session_store=None,
            canary=ctrl,
            request_logger=_FakeLogger(),
            audit_publisher=_FakePublisher(),
            recent_requests=[],
            record_request=lambda *a, **kw: None,
        )
        from meridian.api.finalize import finalize_request

        b = _backend("b1", tags=["canary"])
        finalize_request(
            state=st,
            request_id="r1",
            model="m",
            stream=False,
            backend=b,
            status_code=502,
            start=1000.0,
            error_type="upstream_5xx",
            request_ctx=_ctx(),
            tier_name=None,
            session_route=None,
            org_id=None,
            team_id=None,
        )
        assert len(ctrl._window) == 1
        assert ctrl._window[0][1] is False  # 5xx recorded as error
