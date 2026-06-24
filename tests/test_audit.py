"""Tests for the audit pipeline components: hash chain, Merkle tree, signer,
consumer durability, archiver Object Lock, and the non-blocking publisher."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meridian.audit.archiver import S3Archiver
from meridian.audit.consumer import AuditConsumer
from meridian.audit.hash_chain import GENESIS_HASH, HashChain
from meridian.audit.merkle import MerkleTree
from meridian.audit.publisher import AuditEvent, AuditEventPublisher
from meridian.audit.signer import (
    MerkleRootSigner,
    SignedMerkleRoot,
    generate_keypair,
    load_public_key,
    verify_signed_root,
)
from meridian.config.models import AuditBusConfig


# ---------------------------------------------------------------------------
# Hash Chain
# ---------------------------------------------------------------------------

class TestHashChain:
    def test_empty_chain_verifies(self) -> None:
        chain = HashChain()
        assert chain.verify()
        assert chain.head == GENESIS_HASH
        assert chain.length == 0

    def test_single_append(self) -> None:
        chain = HashChain()
        event = {"request_id": "mrdn-abc123", "status_code": 200}
        entry = chain.append(event)
        assert entry.index == 0
        assert entry.prev_hash == GENESIS_HASH
        assert entry.hash != GENESIS_HASH
        assert chain.head == entry.hash
        assert chain.length == 1
        assert chain.verify()

    def test_chain_of_many(self) -> None:
        chain = HashChain()
        for i in range(100):
            chain.append({"i": i})
        assert chain.length == 100
        assert chain.verify()

    def test_tamper_detection(self) -> None:
        chain = HashChain()
        for i in range(10):
            chain.append({"i": i})
        assert chain.verify()

        # Tamper with an event in the middle.
        chain._entries[5].event["i"] = 999
        assert not chain.verify()

    def test_reset(self) -> None:
        chain = HashChain()
        chain.append({"a": 1})
        chain.append({"b": 2})
        assert chain.length == 2
        chain.reset()
        assert chain.length == 0
        assert chain.head == GENESIS_HASH
        assert chain.verify()


# ---------------------------------------------------------------------------
# Merkle Tree
# ---------------------------------------------------------------------------

class TestMerkleTree:
    def test_empty_tree(self) -> None:
        tree = MerkleTree.build([])
        assert tree.root == ""

    def test_single_leaf(self) -> None:
        tree = MerkleTree.build(["abc123"])
        assert tree.root != ""
        # Root of a single leaf tree is the hash of leaf+leaf.
        assert len(tree.root) == 64  # SHA-256 hex

    def test_two_leaves(self) -> None:
        tree = MerkleTree.build(["aaa", "bbb"])
        assert tree.root != ""
        assert len(tree.root) == 64

    def test_odd_leaves_duplicates_last(self) -> None:
        tree_even = MerkleTree.build(["a", "b", "c", "d"])
        tree_odd = MerkleTree.build(["a", "b", "c"])
        # Different number of leaves → different roots.
        assert tree_even.root != tree_odd.root

    def test_audit_proof_verifies(self) -> None:
        leaves = [f"leaf-{i}" for i in range(8)]
        tree = MerkleTree.build(leaves)

        for i in range(len(leaves)):
            proof = tree.audit_proof(i)
            assert proof is not None
            assert MerkleTree.verify_proof(leaves[i], proof, tree.root)

    def test_audit_proof_rejects_bad_leaf(self) -> None:
        leaves = ["a", "b", "c", "d"]
        tree = MerkleTree.build(leaves)
        proof = tree.audit_proof(0)
        assert proof is not None
        assert not MerkleTree.verify_proof("TAMPERED", proof, tree.root)

    def test_audit_proof_out_of_range(self) -> None:
        tree = MerkleTree.build(["x"])
        assert tree.audit_proof(-1) is None
        assert tree.audit_proof(1) is None


# ---------------------------------------------------------------------------
# Ed25519 Signer
# ---------------------------------------------------------------------------

class TestSigner:
    def test_sign_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            priv = f"{tmp}/private.pem"
            pub = f"{tmp}/public.pem"
            generate_keypair(priv, pub)

            signer = MerkleRootSigner.from_file(priv)
            signed = signer.sign("deadbeef" * 8, batch_size=42)
            trusted = load_public_key(pub)

            assert signed.merkle_root == "deadbeef" * 8
            assert signed.batch_size == 42
            assert verify_signed_root(signed, trusted)

    def test_tampered_root_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            priv = f"{tmp}/private.pem"
            pub = f"{tmp}/public.pem"
            generate_keypair(priv, pub)

            signer = MerkleRootSigner.from_file(priv)
            signed = signer.sign("deadbeef" * 8, batch_size=10)
            trusted = load_public_key(pub)

            tampered = SignedMerkleRoot(
                merkle_root="TAMPERED" * 8,
                batch_size=signed.batch_size,
                signature_b64=signed.signature_b64,
                public_key_b64=signed.public_key_b64,
            )
            # Signature is over the canonical (root, batch_size) binding, so a
            # changed root no longer matches even with the legitimate key.
            assert not verify_signed_root(tampered, trusted)

    def test_tampered_batch_size_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            priv = f"{tmp}/private.pem"
            pub = f"{tmp}/public.pem"
            generate_keypair(priv, pub)

            signer = MerkleRootSigner.from_file(priv)
            signed = signer.sign("deadbeef" * 8, batch_size=10)
            trusted = load_public_key(pub)

            tampered = SignedMerkleRoot(
                merkle_root=signed.merkle_root,
                batch_size=signed.batch_size + 1,
                signature_b64=signed.signature_b64,
                public_key_b64=signed.public_key_b64,
            )
            # batch_size is now bound into the signed message.
            assert not verify_signed_root(tampered, trusted)

    def test_forged_root_with_attacker_key_fails(self) -> None:
        """A forged bundle re-signed with an attacker keypair must be rejected.

        Proves the trust model: verification pins the ORIGINAL public key
        out-of-band, so an attacker re-signing a different root with their own
        keypair (and embedding their own public key) cannot pass.
        """
        with tempfile.TemporaryDirectory() as tmp:
            priv = f"{tmp}/private.pem"
            pub = f"{tmp}/public.pem"
            generate_keypair(priv, pub)

            # Legitimate signature establishes the trusted (pinned) key.
            signer = MerkleRootSigner.from_file(priv)
            signed = signer.sign("deadbeef" * 8, batch_size=42)
            trusted = load_public_key(pub)
            assert verify_signed_root(signed, trusted)

            # Attacker generates their own keypair and forges a new bundle.
            attacker_key = Ed25519PrivateKey.generate()
            attacker_signer = MerkleRootSigner(attacker_key)
            forged = attacker_signer.sign("forged00" * 8, batch_size=999)

            # The forged bundle is internally self-consistent (its embedded key
            # matches its own signature) but must fail against the pinned key.
            attacker_pub = attacker_key.public_key()
            assert verify_signed_root(forged, attacker_pub)  # self-consistency
            assert not verify_signed_root(forged, trusted)  # but not authentic

            # Even splicing the attacker's signature/root onto the bundle while
            # keeping the trusted key's identity is rejected (key mismatch).
            spliced = SignedMerkleRoot(
                merkle_root=forged.merkle_root,
                batch_size=forged.batch_size,
                signature_b64=forged.signature_b64,
                public_key_b64=signed.public_key_b64,  # claim the trusted key
            )
            assert not verify_signed_root(spliced, trusted)

    def test_roundtrip_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            priv = f"{tmp}/private.pem"
            pub = f"{tmp}/public.pem"
            generate_keypair(priv, pub)

            signer = MerkleRootSigner.from_file(priv)
            signed = signer.sign("abc" * 20, batch_size=5)
            trusted = load_public_key(pub)

            json_str = signed.to_json()
            restored = SignedMerkleRoot.from_json(json_str)
            assert restored == signed
            assert verify_signed_root(restored, trusted)


# ---------------------------------------------------------------------------
# End-to-end: Chain → Merkle → Sign → Verify
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_full_pipeline(self) -> None:
        """Simulate the full audit pipeline: chain events, build Merkle, sign, verify."""
        # 1. Build hash chain
        chain = HashChain()
        events = [
            {"request_id": f"mrdn-{i:04d}", "status_code": 200, "latency_ms": 42.0}
            for i in range(16)
        ]
        for ev in events:
            chain.append(ev)
        assert chain.verify()

        # 2. Build Merkle tree from chain hashes
        leaf_hashes = [e.hash for e in chain.entries()]
        tree = MerkleTree.build(leaf_hashes)
        assert tree.root

        # 3. Sign the root
        with tempfile.TemporaryDirectory() as tmp:
            priv = f"{tmp}/private.pem"
            pub = f"{tmp}/public.pem"
            generate_keypair(priv, pub)

            signer = MerkleRootSigner.from_file(priv)
            signed = signer.sign(tree.root, batch_size=len(events))
            trusted = load_public_key(pub)
            assert verify_signed_root(signed, trusted)

        # 4. Verify individual leaf proofs
        for i, leaf in enumerate(leaf_hashes):
            proof = tree.audit_proof(i)
            assert proof is not None
            assert MerkleTree.verify_proof(leaf, proof, tree.root)

        # 5. Reset chain (simulating flush)
        chain.reset()
        assert chain.length == 0
        assert chain.verify()


# ---------------------------------------------------------------------------
# S3 Archiver – per-object Object Lock retention (#2)
# ---------------------------------------------------------------------------

class _FakeS3Client:
    def __init__(self) -> None:
        self.put_kwargs: dict = {}

    def put_object(self, **kwargs: object) -> None:
        self.put_kwargs = kwargs


class TestArchiver:
    def test_upload_sets_explicit_compliance_retention(self, monkeypatch) -> None:
        """Every object must be locked independently, not via a bucket default."""
        import meridian.audit.archiver as arch

        fake = _FakeS3Client()
        monkeypatch.setattr(arch.boto3, "client", lambda *a, **k: fake)

        archiver = S3Archiver(bucket="meridian-audit", retention_days=30)
        signed = SignedMerkleRoot(
            merkle_root="a" * 64, batch_size=5, signature_b64="sig", public_key_b64="pub"
        )
        key = archiver.upload(signed)

        assert key.startswith("audit-logs/")
        assert fake.put_kwargs["ObjectLockMode"] == "COMPLIANCE"
        retain_until = fake.put_kwargs["ObjectLockRetainUntilDate"]
        # Must be a tz-aware datetime roughly retention_days in the future.
        assert isinstance(retain_until, datetime)
        assert retain_until.tzinfo is not None
        delta = retain_until - datetime.now(timezone.utc)
        assert timedelta(days=29) < delta <= timedelta(days=30)


# ---------------------------------------------------------------------------
# Audit Consumer – durability: commit-after-archive, retain-on-failure,
# idle time-flush, and final flush on graceful shutdown (#3 #4 #5 #6)
# ---------------------------------------------------------------------------

class _FakeSigner:
    def sign(self, merkle_root: str, batch_size: int) -> SignedMerkleRoot:
        return SignedMerkleRoot(
            merkle_root=merkle_root, batch_size=batch_size,
            signature_b64="sig", public_key_b64="pub",
        )


class _FakeArchiver:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.uploads: list = []

    def upload(self, signed: SignedMerkleRoot) -> str:
        if self.fail:
            raise RuntimeError("s3 unreachable")
        self.uploads.append(signed)
        return "audit-logs/key.json"


class _FakeKafkaConsumer:
    def __init__(self, *, commit_fails: bool = False) -> None:
        self.commits = 0
        self.commit_fails = commit_fails
        self.stopped = False

    async def getmany(self, timeout_ms: int = 0) -> dict:
        await asyncio.sleep(0.005)  # yield like a real poll
        return {}

    async def commit(self) -> None:
        if self.commit_fails:
            raise RuntimeError("commit failed")
        self.commits += 1

    async def stop(self) -> None:
        self.stopped = True


def _consumer_with(signer, archiver, fake_kafka, **kw) -> AuditConsumer:
    c = AuditConsumer(signer=signer, archiver=archiver, **kw)
    c._consumer = fake_kafka  # type: ignore[assignment]
    # start() normally sets this; we bypass start(), so without it the run()
    # loop (`while self._running`) would exit before doing any work.
    c._running = True
    return c


class TestConsumerDurability:
    async def test_flush_commits_after_archive_then_resets(self) -> None:
        fk = _FakeKafkaConsumer()
        c = _consumer_with(_FakeSigner(), _FakeArchiver(), fk)
        c._chain.append({"request_id": "a"})
        c._chain.append({"request_id": "b"})
        await c._flush()
        assert fk.commits == 1          # committed only after successful archive
        assert c._chain.length == 0     # reset only after durable write

    async def test_flush_retains_batch_when_archive_fails(self) -> None:
        fk = _FakeKafkaConsumer()
        c = _consumer_with(_FakeSigner(), _FakeArchiver(fail=True), fk)
        c._chain.append({"request_id": "a"})
        await c._flush()
        assert fk.commits == 0          # never commit offsets on archive failure
        assert c._chain.length == 1     # batch retained for retry, not dropped

    async def test_flush_retains_batch_when_commit_fails(self) -> None:
        fk = _FakeKafkaConsumer(commit_fails=True)
        arch = _FakeArchiver()
        c = _consumer_with(_FakeSigner(), arch, fk)
        c._chain.append({"request_id": "a"})
        await c._flush()
        assert len(arch.uploads) == 1   # archived...
        assert c._chain.length == 1     # ...but retained because commit failed

    async def test_flush_dev_mode_discards_without_committing(self) -> None:
        fk = _FakeKafkaConsumer()
        c = _consumer_with(None, None, fk)  # no signer/archiver
        c._chain.append({"request_id": "a"})
        await c._flush()
        assert fk.commits == 0          # nothing durable, nothing committed
        assert c._chain.length == 0     # explicit ephemeral discard (dev mode)

    async def test_time_flush_fires_during_idle_period(self) -> None:
        """flush_interval_s must bound flush latency even with no new traffic."""
        fk = _FakeKafkaConsumer()  # getmany always returns {}
        arch = _FakeArchiver()
        c = _consumer_with(
            _FakeSigner(), arch, fk, batch_size=1000, flush_interval_s=0.02
        )
        c._chain.append({"request_id": "seed"})  # pending batch, no new messages
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.4)
        c.request_shutdown()
        await asyncio.wait_for(task, timeout=2)
        assert len(arch.uploads) >= 1   # flushed on the timer, not on traffic

    async def test_final_flush_on_graceful_shutdown(self) -> None:
        """A partial batch must be flushed when run() exits via request_shutdown."""
        fk = _FakeKafkaConsumer()
        arch = _FakeArchiver()
        c = _consumer_with(
            _FakeSigner(), arch, fk, batch_size=1000, flush_interval_s=1000.0
        )
        c._chain.append({"request_id": "seed"})
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.05)
        assert len(arch.uploads) == 0   # huge interval: not flushed by timer
        c.request_shutdown()
        await asyncio.wait_for(task, timeout=2)
        assert len(arch.uploads) == 1   # final flush ran on graceful exit
        assert fk.commits == 1


# ---------------------------------------------------------------------------
# Audit Publisher – non-blocking fire-and-forget + single-partition key (#7 #8)
# ---------------------------------------------------------------------------

class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list = []
        self.stopped = False

    async def start(self) -> None:
        pass

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> None:
        self.sent.append((topic, value, key))

    async def stop(self) -> None:
        self.stopped = True


def _started_publisher(fake: _FakeProducer) -> AuditEventPublisher:
    pub = AuditEventPublisher(AuditBusConfig(enabled=True))
    pub._producer = fake  # type: ignore[assignment]
    pub._drain_task = asyncio.create_task(pub._drain_loop())
    return pub


class TestPublisher:
    async def test_publish_only_enqueues_does_not_deliver_inline(self) -> None:
        """publish() must not await the broker on the request path."""
        fake = _FakeProducer()
        pub = AuditEventPublisher(AuditBusConfig(enabled=True))
        pub._producer = fake  # type: ignore[assignment]  # no drain task running
        await pub.publish(AuditEvent(request_id="x"))
        assert fake.sent == []           # delivery is deferred to the drain task
        assert pub._queue.qsize() == 1   # event sits in the bounded queue

    async def test_publish_drops_oldest_when_queue_full(self) -> None:
        pub = AuditEventPublisher(AuditBusConfig(enabled=True))
        pub._producer = object()  # type: ignore[assignment]  # non-None: publish proceeds
        pub._queue = asyncio.Queue(maxsize=2)
        await pub.publish(AuditEvent(request_id="A"))
        await pub.publish(AuditEvent(request_id="B"))
        await pub.publish(AuditEvent(request_id="C"))  # full → drop oldest (A)
        assert pub._queue.qsize() == 2
        remaining = [pub._queue.get_nowait().request_id for _ in range(2)]
        assert remaining == ["B", "C"]

    async def test_all_events_use_constant_partition_key(self) -> None:
        """Constant key → single partition → deterministic chain order."""
        fake = _FakeProducer()
        pub = _started_publisher(fake)
        for i in range(5):
            await pub.publish(AuditEvent(request_id=f"r{i}"))
        await asyncio.wait_for(pub._queue.join(), timeout=2)
        await pub.stop()
        assert len(fake.sent) == 5
        assert all(key == b"meridian-audit" for (_t, _v, key) in fake.sent)

    async def test_stop_flushes_pending_then_stops_producer(self) -> None:
        fake = _FakeProducer()
        pub = _started_publisher(fake)
        for i in range(3):
            await pub.publish(AuditEvent(request_id=f"r{i}"))
        await pub.stop()
        assert len(fake.sent) == 3       # best-effort flush delivered everything
        assert fake.stopped is True
        assert pub._producer is None

    async def test_disabled_publisher_is_safe_noop(self) -> None:
        pub = AuditEventPublisher(AuditBusConfig(enabled=False))
        await pub.start()                # no real producer created
        assert pub._producer is None
        await pub.publish(AuditEvent(request_id="x"))  # no-op, no crash
        await pub.stop()                 # safe
