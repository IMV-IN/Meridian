"""Application runtime state — single object instead of module globals."""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

from meridian.api.ratelimitter import RateLimitStore
from meridian.audit.publisher import AuditEventPublisher
from meridian.auth import IdentityContext, build_key_index
from meridian.config.models import MeridianConfig
from meridian.cost import CostLedger, InMemoryCostLedger, SqliteCostLedger
from meridian.health.checker import HealthChecker
from meridian.metrics.collectors import BACKEND_HEALTHY, BACKEND_INFLIGHT
from meridian.metrics.logger import RequestLogger
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.affinity import SessionStore
from meridian.router.strategies import RoutingStrategy, create_strategy
from meridian.telemetry import JsonTelemetryAdapter, TelemetryAdapter, TelemetryPoller
from meridian.usage import InMemoryUsageMeter, SqliteUsageMeter, UsageMeter
from meridian.util.helpers import now_ms

logger = logging.getLogger("meridian")


@dataclass
class AppState:
    """All gateway runtime dependencies. Built once per process / test."""

    config: MeridianConfig
    registry: BackendRegistry
    strategy: RoutingStrategy
    health_checker: HealthChecker
    telemetry_poller: TelemetryPoller
    request_logger: RequestLogger
    audit_publisher: AuditEventPublisher
    key_index: Dict[str, IdentityContext]
    rate_limit: RateLimitStore
    usage_meter: Optional[UsageMeter] = None
    cost_ledger: Optional[CostLedger] = None
    session_store: Optional[SessionStore] = None
    recent_requests: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=100)
    )

    def record_request(
        self,
        request_id: str,
        model: str,
        stream: bool,
        backend: str,
        status_code: int,
        latency_ms: float,
        error_type: Optional[str],
    ) -> None:
        self.recent_requests.appendleft({
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "stream": stream,
            "chosen_backend": backend,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
            "error_type": error_type,
        })


def load_config(path: Optional[str] = None) -> MeridianConfig:
    cfg_path = path or os.environ.get("MERIDIAN_CONFIG", "config.yaml")
    if os.path.exists(cfg_path):
        return MeridianConfig.from_yaml(cfg_path)
    return MeridianConfig()


async def build_app_state(
    config: Optional[MeridianConfig] = None,
    *,
    start_background: bool = True,
) -> AppState:
    """Construct runtime state. ``start_background`` starts health/telemetry/audit."""
    cfg = config or load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    key_index = build_key_index(cfg.auth)
    if cfg.auth.enabled:
        logger.info("API-key auth enabled — %d key(s) loaded", len(key_index))

    rate_limit = RateLimitStore(
        max_keys=cfg.rate_limit.max_buckets,
        idle_ttl_s=cfg.rate_limit.idle_ttl_s,
    )

    usage_meter: Optional[UsageMeter] = None
    if cfg.budgets.enabled:
        if cfg.budgets.store == "memory":
            usage_meter = InMemoryUsageMeter()
            logger.info("Tenant budgets enabled — in-memory store")
        else:
            usage_meter = SqliteUsageMeter(cfg.budgets.sqlite_path)
            logger.info("Tenant budgets enabled — sqlite at %s", cfg.budgets.sqlite_path)

    cost_ledger: Optional[CostLedger] = None
    if cfg.cost.enabled:
        if cfg.cost.store == "memory":
            cost_ledger = InMemoryCostLedger()
            logger.info("Cost attribution enabled — in-memory ledger")
        else:
            cost_ledger = SqliteCostLedger(cfg.cost.sqlite_path)
            logger.info("Cost attribution enabled — sqlite at %s", cfg.cost.sqlite_path)

    backends = [Backend(bc) for bc in cfg.backends]
    registry = BackendRegistry(backends)
    logger.info("Loaded %d backend(s): %s", len(backends), [b.name for b in backends])

    strategy = create_strategy(
        cfg.gateway.strategy,
        prefill_weight=cfg.gateway.prefill_weight,
        decode_weight=cfg.gateway.decode_weight,
        default_max_tokens=cfg.gateway.default_max_tokens,
        queue_weight=cfg.gateway.queue_weight,
        mem_weight=cfg.gateway.mem_weight,
    )
    logger.info("Routing strategy: %s", cfg.gateway.strategy)

    for b in backends:
        BACKEND_HEALTHY.labels(backend=b.name).set(1)
        BACKEND_INFLIGHT.labels(backend=b.name).set(0)

    health_checker = HealthChecker(registry, cfg.health)
    if start_background:
        await health_checker.start()

    adapters: Dict[str, TelemetryAdapter] = {}
    poll_interval = 5.0
    for bc in cfg.backends:
        if bc.telemetry is None:
            continue
        if bc.telemetry.type == "json":
            adapters[bc.name] = JsonTelemetryAdapter(
                url=bc.telemetry.url, timeout_s=bc.telemetry.timeout_s
            )
            poll_interval = min(poll_interval, bc.telemetry.interval_s)
        else:
            logger.warning(
                "Backend %s: unknown telemetry type %r, skipping.",
                bc.name, bc.telemetry.type,
            )
    telemetry_poller = TelemetryPoller(registry, adapters, interval_s=poll_interval)
    if start_background:
        await telemetry_poller.start()

    request_logger = RequestLogger(cfg.logging.jsonl_path)
    audit_publisher = AuditEventPublisher(cfg.audit_bus)
    await audit_publisher.start()

    session_store: Optional[SessionStore] = None
    if cfg.session_affinity.enabled:
        session_store = SessionStore(
            ttl_ms=cfg.session_affinity.ttl_s * 1000,
            max_sessions=cfg.session_affinity.max_sessions,
            clock=now_ms,
        )
        logger.info(
            "Session affinity enabled — ttl=%ds, max=%d",
            cfg.session_affinity.ttl_s,
            cfg.session_affinity.max_sessions,
        )

    return AppState(
        config=cfg,
        registry=registry,
        strategy=strategy,
        health_checker=health_checker,
        telemetry_poller=telemetry_poller,
        request_logger=request_logger,
        audit_publisher=audit_publisher,
        key_index=key_index,
        rate_limit=rate_limit,
        usage_meter=usage_meter,
        cost_ledger=cost_ledger,
        session_store=session_store,
    )


async def shutdown_app_state(state: AppState) -> None:
    await state.health_checker.stop()
    await state.telemetry_poller.stop()
    await state.audit_publisher.stop()
    state.request_logger.close()
