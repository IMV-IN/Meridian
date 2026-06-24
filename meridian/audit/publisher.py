"""Async audit event publisher – pushes events to Redpanda/Kafka.

The gateway produces structured audit events for every request (success or
failure).  Publishing is **truly fire-and-forget from the request path**:
``publish()`` only enqueues the event onto a bounded in-memory queue (a
``put_nowait``) and returns effectively instantly, so broker backpressure can
never add to user-facing request latency.  A background drain task owns the
actual ``producer.send_and_wait()`` call and logs delivery failures.  If the
queue overflows (broker slow/unreachable for a sustained period) the oldest
event is dropped with a warning so memory stays bounded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

from meridian.config.models import AuditBusConfig

logger = logging.getLogger("meridian.audit")

# All audit events are routed to a single partition via this constant key so the
# downstream hash chain sees a deterministic, globally-ordered stream.
_AUDIT_PARTITION_KEY = b"meridian-audit"

# Bounded queue size – caps memory if the broker is slow/unreachable.
_MAX_QUEUE_SIZE = 10_000

# Best-effort flush budget on shutdown.
_STOP_FLUSH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit record published to the event bus."""

    request_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model: str = ""
    stream: bool = False
    backend: str = ""
    status_code: int = 0
    latency_ms: float = 0.0
    error_type: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), default=str).encode()


class AuditEventPublisher:
    """Non-blocking async producer around ``aiokafka.AIOKafkaProducer``.

    Events are enqueued by ``publish()`` (request path) and delivered by a
    background drain task, decoupling broker latency from the request path.
    """

    def __init__(self, config: AuditBusConfig) -> None:
        self._config = config
        self._producer: Optional[AIOKafkaProducer] = None
        self._queue: "asyncio.Queue[AuditEvent]" = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._drain_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if not self._config.enabled:
            logger.info("Audit bus disabled – skipping producer start.")
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._config.bootstrap_servers,
            client_id=self._config.client_id,
            # acks="all" guarantees ordering *within a partition*; since every
            # event is keyed by the constant _AUDIT_PARTITION_KEY they all land
            # on one partition, giving a single globally-ordered stream the hash
            # chain can reproduce from scratch.  The audit topic should be
            # provisioned single-partition.
            acks="all",
            max_batch_size=32768,
            linger_ms=50,
        )
        await self._producer.start()
        self._drain_task = asyncio.create_task(self._drain_loop())
        logger.info(
            "Audit producer started – topic=%s, brokers=%s",
            self._config.topic,
            self._config.bootstrap_servers,
        )

    async def publish(self, event: AuditEvent) -> None:
        """Enqueue an event for background delivery.

        Returns effectively instantly: the only work done on the request path is
        a non-blocking ``put_nowait``.  If the queue is full the oldest event is
        dropped (bounded memory) and a warning is logged.  Never blocks on the
        broker.
        """
        if self._producer is None:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
                logger.warning(
                    "Audit queue full – dropped oldest event %s to enqueue %s",
                    dropped.request_id,
                    event.request_id,
                )
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Audit queue full – dropped event %s", event.request_id
                )

    async def _drain_loop(self) -> None:
        """Background task: own the producer and deliver queued events in order."""
        assert self._producer is not None
        while True:
            event = await self._queue.get()
            try:
                await self._send(event)
            finally:
                self._queue.task_done()

    async def _send(self, event: AuditEvent) -> None:
        assert self._producer is not None
        try:
            # Await delivery so real failures under acks="all" surface in logs.
            await self._producer.send_and_wait(
                self._config.topic,
                value=event.to_bytes(),
                key=_AUDIT_PARTITION_KEY,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Failed to deliver audit event %s", event.request_id, exc_info=True
            )

    async def stop(self) -> None:
        # Best-effort flush of queued events within a bounded time budget.
        if self._drain_task is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=_STOP_FLUSH_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning(
                    "Audit queue flush timed out – %d event(s) undelivered",
                    self._queue.qsize(),
                )
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("Audit producer stopped.")
