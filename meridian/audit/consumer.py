"""Audit consumer service – Kafka/Redpanda → Hash Chain → Merkle → Sign → S3.

This is the downstream consumer process that reads audit events from the
``meridian-audit-logs`` topic, builds a tamper-evident hash chain, and
periodically flushes batches as signed Merkle trees to S3.

Architecture:

    Gateway  ──▸  Redpanda Topic  ──▸  AuditConsumer
                                              │
                                         Hash Chain
                                              │
                                         Merkle Tree
                                              │
                                        Ed25519 Sign
                                              │
                                         S3 Object Lock

Run standalone::

    python -m meridian.audit.consumer \\
        --brokers localhost:9092 \\
        --topic meridian-audit-logs \\
        --signing-key ./audit_private.pem \\
        --s3-bucket meridian-audit \\
        --batch-size 64
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Optional

from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

from meridian.audit.archiver import S3Archiver
from meridian.audit.hash_chain import HashChain
from meridian.audit.merkle import MerkleTree
from meridian.audit.signer import MerkleRootSigner

logger = logging.getLogger("meridian.audit.consumer")


class AuditConsumer:
    """Consumes audit events, builds hash chains, flushes Merkle roots."""

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic: str = "meridian-audit-logs",
        group_id: str = "meridian-audit-consumer",
        batch_size: int = 64,
        flush_interval_s: float = 60.0,
        signer: Optional[MerkleRootSigner] = None,
        archiver: Optional[S3Archiver] = None,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._signer = signer
        self._archiver = archiver

        self._chain = HashChain()
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._running = False

    async def start(self) -> None:
        """Start consuming in the background."""
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            auto_offset_reset="earliest",
            # Offsets are committed manually only after a confirmed durable
            # archive, so a crash between commit and flush cannot lose events.
            enable_auto_commit=False,
        )
        await self._consumer.start()
        self._running = True
        logger.info(
            "Audit consumer started – topic=%s, batch_size=%d, flush_interval=%.1fs",
            self._topic,
            self._batch_size,
            self._flush_interval_s,
        )

    async def run(self) -> None:
        """Main consume loop.  Call ``start()`` first.

        Uses ``getmany`` with a bounded timeout instead of ``async for`` so the
        time-based flush condition is evaluated on every iteration even when no
        traffic arrives – this keeps ``flush_interval_s`` an actual upper bound
        on flush latency during idle periods.
        """
        if self._consumer is None:
            raise RuntimeError("Consumer not started – call start() first.")

        last_flush = asyncio.get_running_loop().time()

        # Poll timeout caps how long an idle iteration blocks; keep it well
        # under the flush interval so the time-based flush fires on schedule.
        poll_timeout_ms = max(100, int(min(self._flush_interval_s, 1.0) * 1000))

        try:
            while self._running:
                batches = await self._consumer.getmany(timeout_ms=poll_timeout_ms)

                for _tp, messages in batches.items():
                    for msg in messages:
                        try:
                            event = json.loads(msg.value.decode())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            logger.warning(
                                "Skipping malformed message at offset %d", msg.offset
                            )
                            continue
                        self._chain.append(event)

                # Flush on batch size or time interval – the time check runs even
                # when ``batches`` was empty (idle period).
                now = asyncio.get_running_loop().time()
                if self._chain.length > 0 and (
                    self._chain.length >= self._batch_size
                    or (now - last_flush) >= self._flush_interval_s
                ):
                    await self._flush()
                    last_flush = now
        except asyncio.CancelledError:
            logger.info("Consumer cancelled – performing final flush.")
            if self._chain.length > 0:
                await self._flush()
            raise
        finally:
            # Graceful shutdown (stop() sets _running=False) exits the loop
            # normally; guarantee the last partial batch is still flushed.
            if self._chain.length > 0:
                logger.info("Consumer loop exiting – performing final flush.")
                await self._flush()

    async def _flush(self) -> None:
        """Build Merkle tree, sign root, archive to S3, then commit + reset.

        The chain is reset (and Kafka offsets committed) ONLY after a confirmed
        durable write.  If archival fails, the chain is left intact so the batch
        is retried on the next flush rather than silently dropped.
        """
        entries = self._chain.entries()
        if not entries:
            return

        leaf_hashes = [e.hash for e in entries]
        tree = MerkleTree.build(leaf_hashes)

        logger.info(
            "Flushing batch: %d events, merkle_root=%s",
            len(entries),
            tree.root[:16] + "…",
        )

        # Dev mode: no signer/archiver configured. There is no durable sink, so
        # be explicit that the batch is ephemeral rather than dropping silently.
        if not (self._signer and self._archiver):
            logger.warning(
                "No signer/archiver configured (dev mode) – discarding %d audit "
                "events WITHOUT durable archival.",
                len(entries),
            )
            self._chain.reset()
            return

        if not tree.root:
            logger.error("Empty Merkle root for %d entries – retaining batch.", len(entries))
            return

        signed = self._signer.sign(tree.root, batch_size=len(entries))

        # boto3 put_object is blocking; run it off the event loop so client-facing
        # latency is never affected by audit archival.
        loop = asyncio.get_running_loop()
        try:
            key = await loop.run_in_executor(None, self._archiver.upload, signed)
        except Exception:
            logger.exception(
                "Failed to archive batch of %d events to S3 – batch retained, "
                "will retry on next flush.",
                len(entries),
            )
            return  # Keep chain intact; do NOT commit offsets.

        logger.info("Archived to S3: %s", key)

        # Durable write confirmed – now commit offsets, then reset the chain.
        if self._consumer is not None:
            try:
                await self._consumer.commit()
            except Exception:
                logger.exception(
                    "Archived to S3 but offset commit failed – retaining batch "
                    "to avoid duplicate-free guarantees being broken."
                )
                return  # Archived but not committed; retry keeps at-least-once.

        self._chain.reset()

    def request_shutdown(self) -> None:
        """Signal the consume loop to stop after its next iteration.

        Safe to call from a signal handler. The loop exits because ``_running``
        is False, runs its final flush (which commits offsets), and only then
        is the underlying consumer torn down via :meth:`close`.
        """
        logger.info("Shutdown requested – consumer will drain after final flush.")
        self._running = False

    async def close(self) -> None:
        """Tear down the underlying consumer. Call after ``run()`` returns."""
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None
        logger.info("Audit consumer stopped.")

    async def stop(self) -> None:
        """Request shutdown and tear down (for callers not driving run() directly)."""
        self.request_shutdown()
        await self.close()


async def _main() -> None:
    """CLI entrypoint for standalone consumer."""
    import argparse

    parser = argparse.ArgumentParser(description="Meridian Audit Consumer")
    parser.add_argument("--brokers", default="localhost:9092")
    parser.add_argument("--topic", default="meridian-audit-logs")
    parser.add_argument("--group-id", default="meridian-audit-consumer")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--flush-interval", type=float, default=60.0)
    parser.add_argument("--signing-key", default=None, help="Path to Ed25519 private key PEM")
    parser.add_argument("--s3-bucket", default=None, help="S3 bucket for archival")
    parser.add_argument("--s3-prefix", default="audit-logs/")
    parser.add_argument("--s3-region", default="ap-south-1")
    parser.add_argument("--s3-endpoint", default=None, help="S3 endpoint URL (for MinIO, etc.)")
    args = parser.parse_args()

    signer = MerkleRootSigner.from_file(args.signing_key) if args.signing_key else None
    archiver = (
        S3Archiver(
            bucket=args.s3_bucket,
            prefix=args.s3_prefix,
            region=args.s3_region,
            endpoint_url=args.s3_endpoint,
        )
        if args.s3_bucket
        else None
    )

    consumer = AuditConsumer(
        bootstrap_servers=args.brokers,
        topic=args.topic,
        group_id=args.group_id,
        batch_size=args.batch_size,
        flush_interval_s=args.flush_interval,
        signer=signer,
        archiver=archiver,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Just request shutdown; run()'s graceful exit performs the final flush
        # (and offset commit) before the consumer is closed below.
        loop.add_signal_handler(sig, consumer.request_shutdown)

    await consumer.start()
    try:
        await consumer.run()
    finally:
        await consumer.close()


if __name__ == "__main__":
    asyncio.run(_main())
