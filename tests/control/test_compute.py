"""Meridian Compute: marketplace listings and rentals, per-second billing with
the platform fee, job scheduling across own and rented GPUs, tenant isolation,
API keys, and the desired-snapshot composition nodes consume."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from conftest import MutableClock, NodeKey
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_cloud import CSRF, login, make, workspace

from meridian_control.compute import _aware, fee_for, rental_due
from meridian_control.models import LedgerEntry, Node, Rental

GB = 1024**3
IMAGE = "docker.io/pytorch/pytorch@sha256:" + "a" * 64
ADMIN = "ops@meridian.test"


@pytest.fixture
def clock():
    return MutableClock()


@pytest.fixture
def client(tmp_path, clock):
    return make(tmp_path, clock, admin_emails=(ADMIN,))


class FakeNode:
    """Speaks the node protocol the way meridian-node does: session, heartbeat,
    fetch desired state, post an observation that mirrors the jobs it was given."""

    def __init__(self, app, owner: TestClient, ws: str, gpus: int = 2, jobs_enabled: bool = True,
                 host: str = "10.0.0.5", model: str = "NVIDIA H100", vram_gb: int = 80, name: str = "gpu-host"):
        self.c = TestClient(app)
        self.now = app.state.compute._now  # the test clock: replicas report when they started and finished
        token = owner.post(f"/api/w/{ws}/tokens", json={"auto_approve": True}, headers=CSRF).json()["token"]
        enrolled = self.c.post(
            "/control/v1/enroll", headers={"Authorization": f"Bearer {token}"},
            json={"node_public_key": NodeKey().public_b64url(), "host_claims": {"hostname": name}},
        ).json()
        self.node_id = enrolled["node_id"]
        self.devices = [f"GPU-{self.node_id[-4:]}-{i}" for i in range(gpus)]
        self.jobs_enabled, self.host, self.model, self.vram = jobs_enabled, host, model, vram_gb * GB
        self.seq = 0
        self.busy: set[str] = set()  # GPUs in use by something other than a job, e.g. an engine
        self.phases: dict[tuple[str, int], dict] = {}
        self.desired: dict = {}
        r = self.c.post(f"/control/v1/nodes/{self.node_id}/sessions", json={"agent_session_id": "s1"})
        self.epoch = r.json()["fencing_epoch"]
        self.sync()

    def sync(self) -> dict:
        """One agent tick."""
        self.seq += 1
        hb = self.c.post(f"/control/v1/nodes/{self.node_id}/heartbeat", json={
            "agent_session_id": "s1", "fencing_epoch": self.epoch, "sequence": self.seq,
            "gpus": [
                {"device_id": d, "kind": "physical", "model": self.model, "memory_total_bytes": self.vram}
                for d in self.devices
            ],
        })
        assert hb.status_code == 200, hb.text
        if hb.json()["desired_generation"] > 0:
            self.desired = self.c.get(f"/control/v1/nodes/{self.node_id}/desired-state").json()
        wanted = {(j["job_id"], j["replica"]): j for j in self.desired.get("jobs", [])}
        for key in [k for k in self.phases if k not in wanted]:
            del self.phases[key]  # dropped from the snapshot: the agent forgets it
        for key, j in wanted.items():
            st = self.phases.setdefault(
                key, {"phase": "Running", "exit_code": None, "log_tail": "", "started_at": self.now().isoformat()}
            )
            if j["desired_phase"] != "Running" and st["phase"] == "Running":
                st.update(phase="Cancelled", finished_at=self.now().isoformat())
        held = set(self.busy) | {
            d for k, st in self.phases.items() if st["phase"] == "Running" for d in wanted[k]["device_ids"]
        }
        obs = self.c.post(f"/control/v1/nodes/{self.node_id}/observations", json={
            "node_id": self.node_id, "agent_session_id": "s1", "sequence": self.seq,
            "observed_generation": self.desired.get("generation", 0), "engines": [],
            "jobs": [
                {
                    "job_id": k[0], "replica": k[1], **st,
                    "endpoint": f"http://{self.host}:{wanted[k]['port']}"
                    if wanted[k]["kind"] == "service" and st["phase"] == "Running" else "",
                }
                for k, st in self.phases.items()
            ],
            "capacity": {
                "allocatable_vram_bytes": {d: (0 if d in held else self.vram) for d in self.devices},
                "free_devices": [d for d in self.devices if d not in held],
                "jobs_enabled": self.jobs_enabled, "advertise_host": self.host, "job_port_range": [9000, 9004],
            },
        })
        assert obs.status_code == 200, obs.text
        return self.desired

    def finish(self, job_id: str, exit_code: int = 0, rank: int = 0, logs: str = "") -> None:
        self.phases[(job_id, rank)].update(
            phase="Succeeded" if exit_code == 0 else "Failed", exit_code=exit_code, log_tail=logs,
            message="" if exit_code == 0 else f"exit code {exit_code}", finished_at=self.now().isoformat(),
        )
        self.sync()

    def jobs(self) -> list[dict]:
        return self.desired.get("jobs", [])


class World:
    """A provider with one 2-GPU node listed at ₹100/GPU-hour, and a consumer."""

    def __init__(self, client, tmp_path, clock, price=100_00, credit=10_000_00):
        self.app, self.clock = client.app, clock
        self.provider = login(client, tmp_path, "dc@provider.in", clock)
        self.pws = workspace(self.provider, "Mumbai DC")
        self.consumer = login(client, tmp_path, "ml@startup.in", clock)
        self.cws = workspace(self.consumer, "Startup AI")
        self.ops = login(client, tmp_path, ADMIN, clock)
        self.node = FakeNode(client.app, self.provider, self.pws)
        r = self.provider.post(f"/api/w/{self.pws}/market/offers", headers=CSRF, json={
            "node_id": self.node.node_id, "title": "2× H100, Mumbai", "region": "Mumbai",
            "price_paise_per_gpu_hour": price,
        })
        assert r.status_code == 201, r.text
        self.offer = r.json()
        if credit:
            self.credit(self.cws, credit, "UTR-0001")

    def credit(self, ws, amount, ref):
        r = self.ops.post("/api/platform/credits", headers=CSRF,
                          json={"workspace": ws, "amount_paise": amount, "reference": ref})
        assert r.status_code == 201, r.text

    def tick(self, seconds=0.0):
        self.clock.advance(seconds)
        self.node.sync()  # keep the lease alive across the jump
        self.app.state.compute.tick()

    def balance(self, c, ws):
        return c.get(f"/api/w/{ws}/billing").json()

    def rent(self):
        r = self.consumer.post(f"/api/w/{self.cws}/market/rentals", headers=CSRF, json={"offer_id": self.offer["id"]})
        assert r.status_code == 201, r.text
        return r.json()

    def submit(self, c=None, ws=None, **body):
        c, ws = c or self.consumer, ws or self.cws
        return c.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, **body})


@pytest.fixture
def world(client, tmp_path, clock):
    return World(client, tmp_path, clock)


# ── money ─────────────────────────────────────────────────────────────────


def test_money_is_integer_paise_and_the_fee_is_two_percent():
    assert rental_due(100_00, 2, 3600) == 200_00
    assert rental_due(100_00, 2, 1) == 5  # ₹0.0555… rounds down to 5 paise
    assert rental_due(99_99, 1, 1800) == 49_99
    assert fee_for(200_00, 200) == 4_00
    assert fee_for(49_99, 200) == 1_00  # 99.98 paise rounds half up
    assert fee_for(24, 200) == 0 and fee_for(25, 200) == 1


# ── listings ──────────────────────────────────────────────────────────────


def test_offer_describes_the_real_hardware_and_shows_the_fee(world):
    o = world.offer
    assert o["gpu_model"] == "NVIDIA H100" and o["gpu_count"] == 2 and o["vram_bytes_per_gpu"] == 80 * GB
    assert o["hourly_paise"] == 200_00 and o["hourly_fee_paise"] == 4_00 and o["hourly_total_paise"] == 204_00
    assert o["available"] is True and o["mine"] is True and o["node_id"] == world.node.node_id

    seen = world.consumer.get(f"/api/w/{world.cws}/market/offers").json()["offers"]
    assert len(seen) == 1 and seen[0]["mine"] is False
    assert "node_id" not in seen[0] and "device_ids" not in seen[0]  # the host's identity stays with its owner
    assert seen[0]["provider"]["name"] == "Mumbai DC"


def test_listing_rules(client, tmp_path, clock, world):
    p, pws = world.provider, world.pws
    body = {"node_id": world.node.node_id, "title": "again", "region": "Pune", "price_paise_per_gpu_hour": 5000}
    assert p.post(f"/api/w/{pws}/market/offers", headers=CSRF, json=body).json()["error"]["code"] == "ALREADY_LISTED"

    # Another workspace cannot list a node it does not own, and cannot see whether it exists.
    r = world.consumer.post(f"/api/w/{world.cws}/market/offers", headers=CSRF, json=body)
    assert r.status_code == 404

    no_jobs = FakeNode(client.app, p, pws, jobs_enabled=False)
    r = p.post(f"/api/w/{pws}/market/offers", headers=CSRF, json={**body, "node_id": no_jobs.node_id})
    assert r.json()["error"]["code"] == "JOBS_DISABLED"

    for bad in ({"price_paise_per_gpu_hour": 0}, {"price_paise_per_gpu_hour": 10.5}, {"title": "x"}, {"region": ""}):
        other = FakeNode(client.app, p, pws)
        r = p.post(f"/api/w/{pws}/market/offers", headers=CSRF, json={**body, "node_id": other.node_id, **bad})
        assert r.status_code == 400, bad

    nodes = {n["node_id"]: n for n in p.get(f"/api/w/{pws}/market/listable-nodes").json()["nodes"]}
    assert nodes[world.node.node_id]["reason"] == "already listed"
    assert "jobs are not enabled" in nodes[no_jobs.node_id]["reason"]

    # Operators can run work but not put hardware on sale.
    invite = {"email": "op@provider.in", "role": "operator"}
    world.provider.post(f"/api/w/{pws}/invitations", headers=CSRF, json=invite)
    op = login(client, tmp_path, "op@provider.in", clock)
    assert op.post(f"/api/w/{pws}/market/offers", headers=CSRF, json=body).status_code == 403


def test_pause_reprice_and_retire(world):
    p, pws, oid = world.provider, world.pws, world.offer["id"]
    r = p.patch(f"/api/w/{pws}/market/offers/{oid}", headers=CSRF, json={"status": "paused"})
    assert r.json()["available"] is False
    assert world.consumer.get(f"/api/w/{world.cws}/market/offers").json()["offers"] == []
    r = world.consumer.post(f"/api/w/{world.cws}/market/rentals", headers=CSRF, json={"offer_id": oid})
    assert r.status_code == 404

    relist = {"status": "listed", "price_paise_per_gpu_hour": 50_00}
    p.patch(f"/api/w/{pws}/market/offers/{oid}", headers=CSRF, json=relist)
    rental = world.rent()
    assert rental["price_paise_per_gpu_hour"] == 50_00
    # A later price change never reaches a running rental.
    p.patch(f"/api/w/{pws}/market/offers/{oid}", headers=CSRF, json={"price_paise_per_gpu_hour": 900_00})
    world.tick(3600)
    mine = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert mine["amount_paise"] == 100_00 + 2_00

    assert p.delete(f"/api/w/{pws}/market/offers/{oid}", headers=CSRF).status_code == 409  # rented
    world.consumer.post(f"/api/w/{world.cws}/market/rentals/{rental['id']}/release", headers=CSRF)
    assert p.delete(f"/api/w/{pws}/market/offers/{oid}", headers=CSRF).status_code == 204
    assert p.get(f"/api/w/{pws}/market/offers?mine=true").json()["offers"] == []


# ── renting and billing ───────────────────────────────────────────────────


def test_rental_bills_per_second_provider_price_plus_two_percent(world):
    rental = world.rent()
    assert rental["role"] == "consumer" and rental["hourly_total_paise"] == 204_00

    world.tick(1800)  # half an hour: 2 GPUs × ₹100 × 0.5 h = ₹100, fee ₹2
    c = world.balance(world.consumer, world.cws)
    assert c["balance_paise"] == 10_000_00 - 100_00 - 2_00
    assert c["totals_paise"] == {"topup": 10_000_00, "rental_charge": -100_00, "platform_fee": -2_00}
    assert c["burn_paise_per_hour"] == 204_00
    p = world.balance(world.provider, world.pws)
    assert p["balance_paise"] == 100_00 and p["totals_paise"] == {"rental_earning": 100_00}

    world.clock.advance(90)  # accrues, but below the metering interval: nothing posted yet
    c = world.balance(world.consumer, world.cws)
    assert c["balance_paise"] == 10_000_00 - 102_00
    assert c["accrued_spend_paise"] == 5_00 + 10  # ₹5 for 90 s, 10 paise fee
    assert c["available_paise"] == c["balance_paise"] - 5_10

    done = world.consumer.post(f"/api/w/{world.cws}/market/rentals/{rental['id']}/release", headers=CSRF).json()
    assert done["status"] == "ended" and done["end_reason"] == "released" and done["seconds"] == 1890
    assert done["amount_paise"] == 105_00 + 2_10

    summary = world.ops.get("/api/platform/summary").json()
    assert summary["fee_revenue_paise"] == 2_10 and summary["gross_rental_paise"] == 105_00
    # Every paisa is accounted for: consumer + provider + platform = what was paid in.
    total = sum(w["balance_paise"] for w in summary["workspaces"]) + summary["fee_revenue_paise"]
    assert total == 10_000_00

    world.tick(7200)  # an ended rental accrues nothing more
    assert world.balance(world.consumer, world.cws)["balance_paise"] == 10_000_00 - 107_10


def test_metering_never_drifts_over_many_passes(world):
    world.rent()
    for _ in range(100):
        world.tick(601)  # 16.69… paise-hours each pass; the remainder must carry
    c = world.balance(world.consumer, world.cws)
    due = rental_due(100_00, 2, 60_100)
    assert c["totals_paise"]["rental_charge"] == -due
    assert c["totals_paise"]["platform_fee"] == -fee_for(due, 200)


def test_renting_needs_an_hour_of_credit_and_one_tenant_at_a_time(client, tmp_path, clock):
    w = World(client, tmp_path, clock, credit=0)
    r = w.consumer.post(f"/api/w/{w.cws}/market/rentals", headers=CSRF, json={"offer_id": w.offer["id"]})
    assert r.status_code == 402 and r.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    w.credit(w.cws, 203_99, "UTR-A")
    r = w.consumer.post(f"/api/w/{w.cws}/market/rentals", headers=CSRF, json={"offer_id": w.offer["id"]})
    assert r.status_code == 402  # one paisa short
    w.credit(w.cws, 1, "UTR-B")
    w.rent()

    other = login(client, tmp_path, "second@lab.in", clock)
    ows = workspace(other, "Second Lab")
    w.credit(ows, 1000_00, "UTR-C")
    r = other.post(f"/api/w/{ows}/market/rentals", headers=CSRF, json={"offer_id": w.offer["id"]})
    assert r.status_code == 409 and "rented" in r.json()["error"]["message"]
    r = w.provider.post(f"/api/w/{w.pws}/market/rentals", headers=CSRF, json={"offer_id": w.offer["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "OWN_LISTING"


def test_rental_ends_when_credit_runs_out_and_its_jobs_stop(client, tmp_path, clock):
    w = World(client, tmp_path, clock, credit=204_00)  # exactly one hour
    rental = w.rent()
    job = w.submit(rental_id=rental["id"]).json()
    w.node.sync()
    w.tick(3000)
    assert w.consumer.get(f"/api/w/{w.cws}/market/rentals").json()["rentals"][0]["status"] == "active"
    w.tick(700)  # past the hour
    ended = w.consumer.get(f"/api/w/{w.cws}/market/rentals").json()["rentals"][0]
    assert ended["status"] == "ended" and ended["end_reason"] == "insufficient_funds"
    j = w.consumer.get(f"/api/w/{w.cws}/jobs/{job['id']}").json()
    assert j["status"] == "failed" and "insufficient funds" in j["message"]
    # The overdraft is bounded by one tick, and the node is told to stop.
    assert -10_00 < w.balance(w.consumer, w.cws)["balance_paise"] <= 0
    assert w.node.sync()["jobs"][0]["desired_phase"] == "Stopped"
    assert "jobs" not in w.node.sync()  # confirmed stopped: dropped, GPU free again
    assert w.provider.get(f"/api/w/{w.pws}/market/offers?mine=true").json()["offers"][0]["available"] is True


def test_provider_can_end_a_rental_and_is_paid_for_the_time_used(world):
    rental = world.rent()
    world.tick(600)
    r = world.provider.post(f"/api/w/{world.pws}/market/rentals/{rental['id']}/release", headers=CSRF).json()
    assert r["role"] == "provider" and r["end_reason"] == "provider_ended"
    assert r["amount_paise"] == rental_due(100_00, 2, 600) and r["fee_paise"] == 0  # a provider never pays the fee
    assert world.balance(world.provider, world.pws)["balance_paise"] == rental_due(100_00, 2, 600)
    # A stranger can neither see nor end it.
    assert world.ops.post(f"/api/w/{world.cws}/market/rentals/{rental['id']}/release", headers=CSRF).status_code == 404


def test_platform_credit_and_payout_are_admin_only_and_idempotent_by_reference(world):
    body = {"workspace": world.cws, "amount_paise": 500_00, "reference": "UTR-0001"}
    assert world.consumer.post("/api/platform/credits", headers=CSRF, json=body).status_code == 404
    assert world.consumer.get("/api/platform/summary").status_code == 404
    assert world.ops.post("/api/platform/credits", headers=CSRF, json=body).status_code == 409  # same payment twice
    assert world.ops.post("/api/platform/credits", headers=CSRF, json={**body, "amount_paise": -5}).status_code == 400
    assert world.ops.post("/api/platform/credits", headers=CSRF, json={**body, "reference": ""}).status_code == 400
    assert world.ops.get("/api/me").json()["platform_admin"] is True
    assert world.consumer.get("/api/me").json()["platform_admin"] is False

    world.rent()
    world.tick(3600)
    pay = {"workspace": "mumbai-dc", "amount_paise": 150_00, "reference": "NEFT-77"}  # by slug
    assert world.ops.post("/api/platform/payouts", headers=CSRF, json=pay).json()["balance_paise"] == 50_00
    too_much = {**pay, "reference": "NEFT-78", "amount_paise": 60_00}
    over = world.ops.post("/api/platform/payouts", headers=CSRF, json=too_much)
    assert over.status_code == 409
    assert world.balance(world.provider, world.pws)["totals_paise"] == {"rental_earning": 200_00, "payout": -150_00}


def test_a_payout_reference_can_be_used_only_once(world):
    world.rent()
    world.tick(3600)
    pay = {"workspace": world.pws, "amount_paise": 10_00, "reference": "NEFT-77"}
    assert world.ops.post("/api/platform/payouts", headers=CSRF, json=pay).status_code == 201
    again = world.ops.post("/api/platform/payouts", headers=CSRF, json=pay)
    assert again.status_code == 409 and again.json()["error"]["code"] == "DUPLICATE_REFERENCE"
    assert world.balance(world.provider, world.pws)["totals_paise"]["payout"] == -10_00


def test_release_racing_tick_never_double_charges(world):
    comp = world.app.state.compute
    rental = world.rent()
    world.tick(3600)
    stale = comp._sf()
    r = stale.get(Rental, rental["id"])  # a request thread loads the rental...
    world.clock.advance(700)
    world.node.sync()
    comp.tick()  # ...then the ticker meters and commits
    world.clock.advance(100)
    comp._end_rental(stale, r, "released", comp._now())  # and the request finishes on its stale copy
    stale.commit()
    stale.close()

    due = rental_due(100_00, 2, 3600 + 700 + 100)
    with comp._sf() as s:
        row = s.get(Rental, rental["id"])
        assert row.status == "ended" and row.billed_paise == due and row.fee_paise == fee_for(due, 200)
        charged = s.scalar(select(func.sum(LedgerEntry.amount_paise)).where(
            LedgerEntry.workspace_id == world.cws, LedgerEntry.kind == "rental_charge"))
        everything = s.scalar(select(func.sum(LedgerEntry.amount_paise)))
    assert charged == -due
    assert everything == 10_000_00  # consumer + provider + platform still add up to the credit


def test_rental_ends_at_the_nodes_last_contact_when_it_goes_silent(world):
    comp = world.app.state.compute
    rental = world.rent()
    world.tick(600)
    with comp._sf() as s:
        lease = _aware(s.get(Node, world.node.node_id).lease_expires_at)
    world.clock.advance(3 * 3600)  # the node never reports again
    comp.tick()
    mine = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert mine["status"] == "ended" and mine["end_reason"] == "node_lost"
    with comp._sf() as s:
        row = s.get(Rental, rental["id"])
        assert _aware(row.ended_at) == lease
        seconds = int((lease - _aware(row.started_at)).total_seconds())
        assert seconds < 3 * 3600 and row.billed_paise == rental_due(100_00, 2, seconds)


def test_gpus_the_provider_is_using_cannot_be_listed_rented_or_published_over(client, tmp_path, clock, world):
    p, pws = world.provider, world.pws
    other = FakeNode(client.app, p, pws, name="second")
    other.busy = {other.devices[0]}
    other.sync()
    body = {"node_id": other.node_id, "title": "second host", "region": "Pune", "price_paise_per_gpu_hour": 5000}
    r = p.post(f"/api/w/{pws}/market/offers", headers=CSRF, json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEVICES_BUSY"
    other.busy.clear()
    other.sync()
    assert p.post(f"/api/w/{pws}/market/offers", headers=CSRF, json=body).status_code == 201

    # A listed GPU the provider starts using is not for rent until it is idle again.
    world.node.busy = {world.node.devices[0]}
    world.node.sync()
    mine = p.get(f"/api/w/{pws}/market/offers?mine=true").json()["offers"]
    listing = next(o for o in mine if o["id"] == world.offer["id"])
    assert listing["available"] is False and listing["unavailable_reason"] == "in use by the provider"
    r = world.consumer.post(f"/api/w/{world.cws}/market/rentals", headers=CSRF, json={"offer_id": world.offer["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "UNAVAILABLE"

    # The owner cannot publish an engine onto listed GPUs.
    url = f"/api/w/{pws}/nodes/{world.node.node_id}"
    gen = p.get(url).json()["desired_generation"] + 1
    engine = {"engine_id": "eng_1", "desired_phase": "Running", "resources": {"device_ids": world.node.devices[:1]}}

    def publish(generation, assignment):
        return p.post(f"{url}/desired", headers=CSRF, json={
            "node_id": world.node.node_id, "generation": generation, "snapshot_hash": "sha256:" + "0" * 64,
            "assignments": [assignment],
        })

    r = publish(gen, engine)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEVICES_LISTED"
    assert publish(gen, {**engine, "desired_phase": "Stopped"}).status_code == 200


def test_publishing_a_generation_the_node_already_has_is_rejected_in_cloud_mode(world):
    p, url = world.provider, f"/api/w/{world.pws}/nodes/{world.node.node_id}"
    gen = p.get(url).json()["desired_generation"] + 1

    def publish(generation):
        return p.post(f"{url}/desired", headers=CSRF, json={
            "node_id": world.node.node_id, "generation": generation, "snapshot_hash": "sha256:" + "0" * 64,
            "assignments": [],
        })

    assert publish(gen).status_code == 200
    r = publish(gen)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LOWER_GENERATION"
    assert f"already at generation {gen}" in r.json()["error"]["message"]
    assert publish(gen + 1).status_code == 200


def test_scheduling_and_billing_passes_wait_for_each_other(world):
    comp = world.app.state.compute
    done = threading.Event()

    def tick():
        comp.tick()
        done.set()

    with comp._lock:
        threading.Thread(target=tick, daemon=True).start()
        assert not done.wait(0.3)  # blocked while another pass holds the lock
    assert done.wait(5)


# ── jobs ──────────────────────────────────────────────────────────────────


def test_job_on_your_own_node_is_free_and_runs_to_success(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner, "IIT Lab")
    node = FakeNode(client.app, owner, ws, gpus=1)
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={
        "name": "finetune", "image": IMAGE, "args": ["python", "train.py", "--rank={{rank}}"],
        "env": {"WANDB_MODE": "offline"}, "min_vram_gb": 40, "timeout_seconds": 7200,
    })
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "scheduled" and job["placements"][0]["node_id"] == node.node_id

    spec = node.sync()["jobs"][0]
    assert spec["image"] == IMAGE and spec["args"] == ["python", "train.py", "--rank=0"]
    assert spec["device_ids"] == node.devices and spec["port"] == 0 and spec["timeout_seconds"] == 7200
    assert spec["env"] == {
        "WANDB_MODE": "offline", "MERIDIAN_JOB_ID": job["id"], "MERIDIAN_JOB_NAME": "finetune",
        "MERIDIAN_JOB_RANK": "0", "MERIDIAN_JOB_REPLICAS": "1",
    }
    assert owner.get(f"/api/w/{ws}/jobs/{job['id']}").json()["status"] == "running"

    node.finish(job["id"], 0, logs="epoch 3/3 loss 0.12\n")
    done = owner.get(f"/api/w/{ws}/jobs/{job['id']}").json()
    assert done["status"] == "succeeded" and done["placements"][0]["exit_code"] == 0
    assert done["placements"][0]["log_tail"] == "epoch 3/3 loss 0.12\n"
    assert "jobs" not in node.sync()  # released
    assert owner.get(f"/api/w/{ws}/billing").json()["entries"] == []  # nothing charged
    listed = owner.get(f"/api/w/{ws}/jobs?status=active").json()["jobs"]
    assert listed == [] and owner.get(f"/api/w/{ws}/jobs").json()["jobs"][0]["id"] == job["id"]


def test_failed_replica_fails_the_job_and_reports_why(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=1)
    job = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    node.sync()
    node.finish(job["id"], 137)
    j = owner.get(f"/api/w/{ws}/jobs/{job['id']}").json()
    assert j["status"] == "failed" and j["message"] == "replica 0: exit code 137"


def test_jobs_queue_for_capacity_and_start_when_it_frees(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=1)
    first = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    second = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    assert second["status"] == "queued" and "waiting for capacity" in second["message"]
    big = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, "min_vram_gb": 200}).json()
    node.sync()
    node.finish(first["id"])
    node.sync()  # the GPU is reported free; the queued job takes it
    assert owner.get(f"/api/w/{ws}/jobs/{second['id']}").json()["status"] in ("scheduled", "running")
    assert [j["job_id"] for j in node.sync()["jobs"]] == [second["id"]]
    assert owner.get(f"/api/w/{ws}/jobs/{big['id']}").json()["status"] == "queued"  # no 200 GB GPU exists


def test_cancel_stops_the_container_before_the_gpu_is_reused(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=1)
    job = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    node.sync()
    waiting = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    r = owner.post(f"/api/w/{ws}/jobs/{job['id']}/cancel", headers=CSRF)
    assert r.json()["status"] == "cancelled"
    assert owner.post(f"/api/w/{ws}/jobs/{job['id']}/cancel", headers=CSRF).status_code == 409
    # Until the node confirms the stop, the GPU is still held and the next job waits.
    assert owner.get(f"/api/w/{ws}/jobs/{waiting['id']}").json()["status"] == "queued"
    assert node.sync()["jobs"][0]["desired_phase"] == "Stopped"
    node.sync()
    assert owner.get(f"/api/w/{ws}/jobs/{waiting['id']}").json()["status"] in ("scheduled", "running")


def test_multi_node_training_job_is_placed_all_or_nothing_with_ranks_and_peers(client, tmp_path, clock):
    owner = login(client, tmp_path, "hpc@iisc.ac.in", clock)
    ws = workspace(owner, "IISc HPC")
    a = FakeNode(client.app, owner, ws, gpus=2, host="10.0.0.1", name="a")
    b = FakeNode(client.app, owner, ws, gpus=2, host="10.0.0.2", name="b")
    body = {"image": IMAGE, "replicas": 2, "gpus_per_replica": 2,
            "args": ["torchrun", "--master={{master}}", "--nnodes={{replicas}}", "--node_rank={{rank}}"]}
    job = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json=body).json()
    assert job["status"] == "scheduled" and {p["node_id"] for p in job["placements"]} == {a.node_id, b.node_id}

    r0, r1 = a.sync()["jobs"][0], b.sync()["jobs"][0]
    assert (r0["replica"], r1["replica"]) == (0, 1)
    assert r0["env"]["MERIDIAN_JOB_PEERS"] == r1["env"]["MERIDIAN_JOB_PEERS"] == "10.0.0.1:9000,10.0.0.2:9000"
    assert r1["env"]["MERIDIAN_JOB_MASTER"] == "10.0.0.1:9000" and r1["env"]["MERIDIAN_JOB_RANK"] == "1"
    assert r1["args"] == ["torchrun", "--master=10.0.0.1:9000", "--nnodes=2", "--node_rank=1"]
    assert len(r0["device_ids"]) == 2 and r0["port"] == 9000

    # A second gang job cannot be half-placed: nothing is reserved until all of it fits.
    blocked = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json=body).json()
    assert blocked["status"] == "queued" and blocked["placements"] == []

    # One replica dying fails the job and stops its peer.
    a.finish(job["id"], 1, rank=0)
    assert owner.get(f"/api/w/{ws}/jobs/{job['id']}").json()["status"] == "failed"
    assert b.sync()["jobs"][0]["desired_phase"] == "Stopped"


def test_job_times_out_and_a_lost_node_fails_its_job(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=2)
    slow = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, "timeout_seconds": 60}).json()
    other = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    node.sync()
    clock.advance(61)
    node.sync()
    j = owner.get(f"/api/w/{ws}/jobs/{slow['id']}").json()
    assert j["status"] == "failed" and "timed out" in j["message"]

    clock.advance(30 + 301)  # lease expired and the grace period passed, no heartbeat
    client.app.state.compute.tick()
    j = owner.get(f"/api/w/{ws}/jobs/{other['id']}").json()
    assert j["status"] == "failed" and "stopped reporting" in j["message"]


def test_job_validation(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    bad = [
        {"image": "pytorch/pytorch:latest"},
        {"image": "docker.io/pytorch/pytorch:2.5"},
        {"args": "python train.py"},
        {"env": {"MERIDIAN_JOB_RANK": "9"}},
        {"env": {"bad-name": "x"}},
        {"env": {"A": 1}},
        {"replicas": 0},
        {"replicas": 65},
        {"gpus_per_replica": 17},
        {"kind": "daemon"},
        {"kind": "service", "replicas": 2},
        {"timeout_seconds": -1},
        {"offer_id": "off_x", "rental_id": "rnt_x"},
        {"rental_id": "rnt_missing"},
    ]
    for over in bad:
        r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, **over})
        assert r.status_code in (400, 404), (over, r.text)
    assert owner.get(f"/api/w/{ws}/jobs").json()["jobs"] == []
    assert owner.get(f"/api/w/{ws}/jobs/job_nope").status_code == 404


# ── jobs on rented GPUs ───────────────────────────────────────────────────


def test_run_on_a_listing_rents_for_the_job_and_releases_when_it_finishes(world):
    r = world.submit(name="eval", offer_id=world.offer["id"], gpus_per_replica=2)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "scheduled" and job["placements"][0]["node_id"] == world.node.node_id
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert rental["status"] == "active" and rental["release_after_job_id"] == job["id"]

    world.node.sync()
    world.tick(900)  # 15 minutes of 2 GPUs: ₹50 + ₹1 fee
    world.node.finish(job["id"], 0)
    assert world.consumer.get(f"/api/w/{world.cws}/jobs/{job['id']}").json()["status"] == "succeeded"
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert rental["status"] == "ended" and rental["end_reason"] == "job_finished"
    assert rental["amount_paise"] == 50_00 + 1_00
    assert world.balance(world.consumer, world.cws)["balance_paise"] == 10_000_00 - 51_00
    world.node.sync()
    assert world.consumer.get(f"/api/w/{world.cws}/market/offers").json()["offers"][0]["available"] is True


def test_run_on_a_listing_checks_size_role_and_credit_before_renting(client, tmp_path, clock, world):
    r = world.submit(offer_id=world.offer["id"], gpus_per_replica=4)
    assert r.status_code == 409 and r.json()["error"]["code"] == "TOO_LARGE"
    invite = {"email": "dev@startup.in", "role": "operator"}
    world.consumer.post(f"/api/w/{world.cws}/invitations", headers=CSRF, json=invite)
    dev = login(client, tmp_path, "dev@startup.in", clock)
    r = world.submit(c=dev, offer_id=world.offer["id"])
    assert r.status_code == 403  # an operator cannot spend the workspace's money
    assert world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"] == []
    assert world.consumer.get(f"/api/w/{world.cws}/jobs").json()["jobs"] == []


def test_tenant_work_is_invisible_to_the_host_and_listed_gpus_are_reserved_for_renters(world):
    # The provider's own job stays off GPUs that are on sale.
    own = world.submit(c=world.provider, ws=world.pws).json()
    assert own["status"] == "queued"

    rental = world.rent()
    secret_job = world.submit(
        rental_id=rental["id"], name="secret", args=["python", "proprietary.py"], env={"DATASET": "s3://private"},
    ).json()
    world.node.sync()
    world.node.phases[(secret_job["id"], 0)]["log_tail"] = "customer data in logs\n"
    world.node.sync()

    # The tenant reads its own job in full.
    mine = world.consumer.get(f"/api/w/{world.cws}/jobs/{secret_job['id']}").json()
    assert mine["placements"][0]["log_tail"] == "customer data in logs\n"
    assert mine["args"] == ["python", "proprietary.py"]

    # The host sees that a GPU is taken, and nothing about what runs on it.
    assert world.provider.get(f"/api/w/{world.pws}/jobs/{secret_job['id']}").status_code == 404
    assert world.provider.get(f"/api/w/{world.pws}/jobs").json()["jobs"][0]["id"] == own["id"]
    detail = world.provider.get(f"/api/w/{world.pws}/nodes/{world.node.node_id}").text
    for leak in ("proprietary.py", "s3://private", "customer data in logs", IMAGE, "MERIDIAN_JOB_NAME"):
        assert leak not in detail
    view = world.provider.get(f"/api/w/{world.pws}/nodes/{world.node.node_id}").json()
    assert view["desired"]["snapshot"]["jobs"][0]["tenant"] is True
    seen = view["observation"]["jobs"][0]
    assert seen.pop("started_at") and seen == {
        "job_id": secret_job["id"], "replica": 0, "phase": "Running", "tenant": True,
    }

    # The host publishing engines can neither drop nor replace the tenant's job.
    gen = view["desired_generation"] + 1
    r = world.provider.post(f"/api/w/{world.pws}/nodes/{world.node.node_id}/desired", headers=CSRF, json={
        "node_id": world.node.node_id, "generation": gen, "snapshot_hash": "sha256:" + "0" * 64,
        "assignments": [], "jobs": [{"job_id": "evil", "replica": 0, "kind": "job", "image": IMAGE}],
    })
    assert r.status_code == 200
    desired = world.node.sync()
    assert desired["generation"] == gen and [j["job_id"] for j in desired["jobs"]] == [secret_job["id"]]

    # The tenant cannot reach the host's fleet API at all.
    assert world.consumer.get(f"/api/w/{world.cws}/nodes/{world.node.node_id}").status_code == 404
    assert world.consumer.get(f"/api/w/{world.pws}/nodes").status_code == 404


def test_scheduler_preserves_the_owners_engine_assignments(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=1)
    engine = {"engine_id": "eng_1", "node_id": node.node_id, "generation": 1, "desired_phase": "Running"}
    owner.post(f"/api/w/{ws}/nodes/{node.node_id}/desired", headers=CSRF, json={
        "node_id": node.node_id, "generation": 1, "snapshot_hash": "sha256:" + "0" * 64, "assignments": [engine],
    })
    assert "jobs" not in node.sync()  # no jobs: older agents never see the key
    job = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    snap = node.sync()
    assert snap["generation"] == 2 and snap["assignments"] == [engine] and snap["jobs"][0]["job_id"] == job["id"]
    assert snap["snapshot_hash"].startswith("sha256:")
    node.finish(job["id"])
    snap = node.sync()
    assert snap["generation"] == 3 and snap["assignments"] == [engine] and "jobs" not in snap


def test_service_gets_an_endpoint_and_a_token_only_operators_can_read(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    node = FakeNode(client.app, owner, ws, gpus=1, host="203.0.113.7")
    svc = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={
        "kind": "service", "name": "llama", "image": IMAGE,
        "args": ["--port", "{{port}}", "--api-key", "{{service_token}}"],
    }).json()
    token = svc["service"]["token"]
    assert token.startswith("mst_")
    spec = node.sync()["jobs"][0]
    assert spec["kind"] == "service" and spec["port"] == 9000
    assert spec["args"] == ["--port", "9000", "--api-key", token] and spec["env"]["MERIDIAN_SERVICE_TOKEN"] == token
    node.sync()
    detail = owner.get(f"/api/w/{ws}/jobs/{svc['id']}").json()
    assert detail["status"] == "running" and detail["service"]["endpoint"] == "http://203.0.113.7:9000"

    owner.post(f"/api/w/{ws}/invitations", headers=CSRF, json={"email": "viewer@iit.ac.in", "role": "viewer"})
    viewer = login(client, tmp_path, "viewer@iit.ac.in", clock)
    assert viewer.get(f"/api/w/{ws}/jobs/{svc['id']}").json()["service"]["token"] is None
    assert viewer.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).status_code == 403

    # A service that exits is a failure, not a success.
    node.finish(svc["id"], 0)
    assert owner.get(f"/api/w/{ws}/jobs/{svc['id']}").json()["message"] == "the service exited"


# ── API keys ──────────────────────────────────────────────────────────────


def test_api_keys_authenticate_the_cli_with_a_capped_role(client, tmp_path, clock):
    owner = login(client, tmp_path, "lab@iit.ac.in", clock)
    ws = workspace(owner)
    other_ws = workspace(owner, "Other")
    node = FakeNode(client.app, owner, ws, gpus=1)
    created = owner.post(f"/api/w/{ws}/api-keys", headers=CSRF, json={"name": "ci", "role": "operator"}).json()
    token = created["token"]
    assert token.startswith("mck_") and created["prefix"] == token[:10]
    listed = owner.get(f"/api/w/{ws}/api-keys").json()["keys"]
    assert len(listed) == 1 and "token" not in listed[0]

    cli = TestClient(client.app)  # no cookie, no CSRF header
    auth = {"Authorization": f"Bearer {token}"}
    r = cli.post(f"/api/w/{ws}/jobs", headers=auth, json={"image": IMAGE})
    assert r.status_code == 201 and r.json()["created_by"] == "key:ci"
    assert cli.get(f"/api/w/{ws}/jobs", headers=auth).status_code == 200
    assert cli.get(f"/api/w/{ws}/nodes", headers=auth).json()["nodes"][0]["node_id"] == node.node_id
    # Renting spends money: admin only.
    assert cli.post(f"/api/w/{ws}/market/rentals", headers=auth, json={"offer_id": "x"}).status_code == 403
    assert cli.get(f"/api/w/{other_ws}/jobs", headers=auth).status_code == 401  # bound to one workspace
    assert cli.get("/api/me", headers=auth).status_code == 401  # not a user session
    assert cli.get(f"/api/w/{ws}/jobs", headers={"Authorization": "Bearer mck_wrong"}).status_code == 401
    assert cli.get(f"/api/w/{ws}/members", headers=auth).status_code == 200
    invite = {"email": "x@y.in", "role": "admin"}
    assert cli.post(f"/api/w/{ws}/invitations", headers=auth, json=invite).status_code == 404  # keys manage no members

    assert owner.delete(f"/api/w/{ws}/api-keys/{created['id']}", headers=CSRF).status_code == 204
    assert cli.get(f"/api/w/{ws}/jobs", headers=auth).status_code == 401
    assert owner.post(f"/api/w/{ws}/api-keys", headers=CSRF, json={"name": "x", "role": "owner"}).status_code == 400


def test_compute_overview(world):
    world.rent()
    world.submit().json()
    o = world.consumer.get(f"/api/w/{world.cws}/compute/overview").json()
    assert o["rented_gpus"] == 2 and o["jobs"] == {"scheduled": 1} and o["balance_paise"] == 10_000_00
    p = world.provider.get(f"/api/w/{world.pws}/compute/overview").json()
    assert p["gpus_rented_out"] == 2 and p["listings"] == 1


# ── usage and quotas ──────────────────────────────────────────────────────


class Lab:
    """An institute: a head (owner), a student (operator), a dean (admin) and a viewer, one GPU node."""

    def __init__(self, client, tmp_path, clock, gpus=2):
        self.app, self.clock, self.client, self.tmp_path = client.app, clock, client, tmp_path
        self.head = login(client, tmp_path, "head@iit.ac.in", clock)
        self.ws = workspace(self.head, "IIT Lab")
        self.node = FakeNode(client.app, self.head, self.ws, gpus=gpus)
        for email, role in (("stu@iit.ac.in", "operator"), ("dean@iit.ac.in", "admin"), ("view@iit.ac.in", "viewer")):
            self.head.post(f"/api/w/{self.ws}/invitations", headers=CSRF, json={"email": email, "role": role})
        self.stu = login(client, tmp_path, "stu@iit.ac.in", clock)
        self.dean = login(client, tmp_path, "dean@iit.ac.in", clock)
        self.view = login(client, tmp_path, "view@iit.ac.in", clock)
        members = self.head.get(f"/api/w/{self.ws}/members").json()["members"]
        self.ids = {m["email"]: m["id"] for m in members}
        self.node.sync()  # the sign-ins above moved the clock; renew the node's lease

    def submit(self, c, **body):
        return c.post(f"/api/w/{self.ws}/jobs", headers=CSRF, json={"image": IMAGE, **body})

    def run(self, c, seconds, **body) -> dict:
        """Submit a job, let it run for `seconds`, and let it succeed."""
        r = self.submit(c, **body)
        assert r.status_code == 201, r.text
        job = r.json()
        self.node.sync()
        self.clock.advance(seconds)
        self.node.finish(job["id"])
        return job

    def usage(self, c=None, month=None) -> dict:
        r = (c or self.head).get(f"/api/w/{self.ws}/usage" + (f"?month={month}" if month else ""))
        assert r.status_code == 200, r.text
        return r.json()

    def person(self, email, **kw) -> dict:
        return next(p for p in self.usage(**kw)["people"] if p["email"] == email)

    def set_compute(self, email, **body):
        return self.head.patch(f"/api/w/{self.ws}/members/{self.ids[email]}/compute", headers=CSRF, json=body)


@pytest.fixture
def lab(client, tmp_path, clock):
    return Lab(client, tmp_path, clock)


def test_usage_is_gpu_hours_per_person_with_api_keys_attributed_to_their_creator(lab):
    lab.run(lab.stu, 3600, gpus_per_replica=2)  # 2 GPU-hours
    lab.run(lab.head, 1800)  # 0.5
    key = lab.head.post(f"/api/w/{lab.ws}/api-keys", headers=CSRF, json={"name": "ci", "role": "operator"}).json()
    ci = TestClient(lab.app)
    r = ci.post(f"/api/w/{lab.ws}/jobs", headers={"Authorization": f"Bearer {key['token']}"}, json={"image": IMAGE})
    assert r.json()["created_by"] == "key:ci"
    lab.node.sync()
    lab.clock.advance(3600)
    lab.node.finish(r.json()["id"])

    u = lab.usage()
    assert u["month"] == "2026-08"
    by = {p["email"]: p for p in u["people"]}
    assert (by["stu@iit.ac.in"]["gpu_hours_own"], by["stu@iit.ac.in"]["jobs"]) == (2.0, 1)
    assert (by["head@iit.ac.in"]["gpu_hours_own"], by["head@iit.ac.in"]["jobs"]) == (1.5, 2)  # 0.5 + the key's 1.0
    assert "key:ci" not in by and by["dean@iit.ac.in"]["gpu_hours_own"] == 0.0  # members with no usage are listed
    assert u["totals"] == {"jobs": 3, "gpu_hours_own": 3.5, "gpu_hours_rented": 0.0}

    # Two admins each made a key called "ci": the name no longer says whose it is, so it stays a label.
    lab.dean.post(f"/api/w/{lab.ws}/api-keys", headers=CSRF, json={"name": "ci", "role": "operator"})
    assert lab.person("key:ci")["gpu_hours_own"] == 1.0 and lab.person("head@iit.ac.in")["gpu_hours_own"] == 0.5


def test_usage_splits_rented_from_own_and_groups_roll_up(world):
    world.rent()
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    pinned = world.submit(gpus_per_replica=2, rental_id=rental["id"]).json()
    world.node.sync()
    world.clock.advance(1800)
    world.node.finish(pinned["id"])
    unpinned = world.submit(gpus_per_replica=1).json()  # lands on the rented GPUs: a node this workspace does not own
    world.node.sync()
    world.clock.advance(3600)
    world.node.finish(unpinned["id"])

    me = world.consumer.get(f"/api/w/{world.cws}/usage").json()["people"][0]
    assert me["email"] == "ml@startup.in" and me["gpu_hours_own"] == 0.0
    assert me["gpu_hours_rented"] == 2.0 and me["jobs"] == 2  # 2 GPUs × 0.5 h + 1 GPU × 1 h

    uid = world.consumer.get("/api/me").json()["user"]["id"]
    world.consumer.patch(f"/api/w/{world.cws}/members/{uid}/compute", headers=CSRF, json={"group": "vision-lab"})
    u = world.consumer.get(f"/api/w/{world.cws}/usage").json()
    assert u["groups"] == [
        {"group": "vision-lab", "members": 1, "jobs": 2, "gpu_hours_own": 0.0, "gpu_hours_rented": 2.0}
    ]
    assert u["totals"] == {"jobs": 2, "gpu_hours_own": 0.0, "gpu_hours_rented": 2.0}


def test_usage_is_clipped_to_the_month_and_validates_it(tmp_path):
    clock = MutableClock(datetime(2026, 8, 31, 22, 50, tzinfo=timezone.utc))
    lab = Lab(make(tmp_path, clock, admin_emails=(ADMIN,)), tmp_path, clock)
    clock._now = datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)
    lab.node.sync()
    job = lab.submit(lab.stu).json()
    lab.node.sync()
    clock.advance(3 * 3600)  # until 02:00 on 1 September
    lab.node.finish(job["id"])
    assert lab.person("stu@iit.ac.in", month="2026-08")["gpu_hours_own"] == 1.0
    assert lab.person("stu@iit.ac.in", month="2026-09")["gpu_hours_own"] == 2.0
    assert lab.person("stu@iit.ac.in")["gpu_hours_own"] == 2.0  # defaults to the current UTC month
    assert lab.person("stu@iit.ac.in", month="2026-07")["gpu_hours_own"] == 0.0
    for bad in ("2026-13", "2026-8", "last", "1999-01"):
        assert lab.head.get(f"/api/w/{lab.ws}/usage?month={bad}").status_code == 400, bad
    assert lab.head.get(f"/api/w/{lab.ws}/usage?format=xml").status_code == 400


def test_a_job_that_never_started_uses_no_gpu_hours_and_a_running_one_counts_up_to_now(lab):
    lab.submit(lab.stu, min_vram_gb=500)  # no such GPU: stays queued
    lab.clock.advance(7200)
    assert lab.person("stu@iit.ac.in")["gpu_hours_own"] == 0.0 and lab.person("stu@iit.ac.in")["jobs"] == 0
    lab.node.sync()
    lab.submit(lab.stu)
    lab.node.sync()
    lab.clock.advance(5400)
    assert lab.person("stu@iit.ac.in")["gpu_hours_own"] == 1.5


def test_usage_csv_is_for_finance_and_defuses_spreadsheet_formulas(lab):
    lab.run(lab.stu, 3600)
    lab.set_compute("stu@iit.ac.in", group="=cmd|' /c calc'!A1", gpu_hour_quota=10)
    r = lab.view.get(f"/api/w/{lab.ws}/usage?format=csv")  # a viewer can export
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="meridian-usage-2026-08.csv"'
    lines = r.text.splitlines()
    assert lines[0] == "email,name,group,gpu_hours_own,gpu_hours_rented,jobs,quota_gpu_hours,remaining_gpu_hours"
    stu = next(line for line in lines if line.startswith("stu@iit.ac.in"))
    assert stu.endswith(",1.0,0.0,1,10,9.0") and ",'=cmd" in stu
    head = next(line for line in lines if line.startswith("head@iit.ac.in"))
    assert head.endswith(",0.0,0.0,0,,")  # no quota: empty cells, not "None"


def test_quota_rejects_new_jobs_but_never_kills_a_running_one(lab):
    assert lab.set_compute("stu@iit.ac.in", group="vision-lab", gpu_hour_quota=2).json() == {
        "user_id": lab.ids["stu@iit.ac.in"], "group": "vision-lab", "gpu_hour_quota": 2,
    }
    job = lab.submit(lab.stu).json()
    lab.node.sync()
    lab.clock.advance(3 * 3600)  # three hours on a two-hour quota
    lab.node.sync()
    lab.app.state.compute.tick()
    assert lab.head.get(f"/api/w/{lab.ws}/jobs/{job['id']}").json()["status"] == "running"  # not killed

    r = lab.submit(lab.stu)
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTA_EXCEEDED"
    msg = r.json()["error"]["message"]
    assert "3.00 of 2 GPU-hours" in msg and "2026-09-01" in msg and "2026-08" in msg
    assert lab.person("stu@iit.ac.in")["remaining_gpu_hours"] == 0.0
    assert lab.submit(lab.head).status_code == 201  # no quota, no limit


def test_quota_binds_admins_and_their_keys_and_a_workspace_default_covers_members_without_one(lab):
    lab.run(lab.head, 3600)
    lab.run(lab.dean, 3600)
    key = lab.dean.post(f"/api/w/{lab.ws}/api-keys", headers=CSRF, json={"name": "nightly", "role": "operator"}).json()
    cli = TestClient(lab.app)
    auth = {"Authorization": f"Bearer {key['token']}"}
    key_job = cli.post(f"/api/w/{lab.ws}/jobs", headers=auth, json={"image": IMAGE})
    assert key_job.status_code == 201  # no quota yet
    lab.node.sync()
    lab.clock.advance(3600)
    lab.node.finish(key_job.json()["id"])  # the dean has used 2 GPU-hours, one of them through the key

    default = lab.head.patch(f"/api/w/{lab.ws}/compute-settings", headers=CSRF, json={"default_gpu_hour_quota": 2})
    assert default.json() == {"default_gpu_hour_quota": 2}
    assert lab.usage()["default_gpu_hour_quota"] == 2 and lab.person("stu@iit.ac.in")["quota_gpu_hours"] == 2
    # The dean (an admin) is at the default, and so is the dean's key; the head has used 1 of 2.
    assert lab.submit(lab.dean).json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert cli.post(f"/api/w/{lab.ws}/jobs", headers=auth, json={"image": IMAGE}).json()["error"]["code"] == (
        "QUOTA_EXCEEDED"
    )
    assert lab.submit(lab.head).status_code == 201
    lab.set_compute("dean@iit.ac.in", gpu_hour_quota=100)  # a member's own quota replaces the default
    assert lab.submit(lab.dean).status_code == 201
    # Clearing the default lifts the limit for members without a quota of their own.
    lab.head.patch(f"/api/w/{lab.ws}/compute-settings", headers=CSRF, json={"default_gpu_hour_quota": None})
    assert lab.usage()["default_gpu_hour_quota"] is None and lab.person("stu@iit.ac.in")["quota_gpu_hours"] is None


def test_queued_jobs_wait_for_the_quota_and_start_when_it_is_raised(client, tmp_path, clock):
    lab = Lab(client, tmp_path, clock, gpus=1)
    lab.set_compute("stu@iit.ac.in", gpu_hour_quota=2)
    first = lab.submit(lab.stu).json()
    second = lab.submit(lab.stu).json()
    assert second["status"] == "queued" and "waiting for capacity" in second["message"]
    lab.node.sync()
    clock.advance(3 * 3600)
    lab.node.finish(first["id"])  # the GPU frees, but the student has used 3 of 2 GPU-hours
    lab.node.sync()
    queued = lab.head.get(f"/api/w/{lab.ws}/jobs/{second['id']}").json()
    assert queued["status"] == "queued" and queued["message"] == "waiting: monthly GPU-hour quota used"
    lab.set_compute("stu@iit.ac.in", gpu_hour_quota=10)
    lab.node.sync()
    assert lab.head.get(f"/api/w/{lab.ws}/jobs/{second['id']}").json()["status"] in ("scheduled", "running")


def test_rented_usage_and_pinned_jobs_are_outside_the_own_node_quota(world):
    world.rent()
    uid = world.consumer.get("/api/me").json()["user"]["id"]
    world.consumer.patch(f"/api/w/{world.cws}/members/{uid}/compute", headers=CSRF, json={"gpu_hour_quota": 0})
    # Quota 0 closes the workspace's own nodes. A job pinned to a rental is paid for with credit, so it runs.
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert world.submit(rental_id=rental["id"]).status_code == 201
    r = world.submit()
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTA_EXCEEDED"
    world.consumer.post(f"/api/w/{world.cws}/market/rentals/{rental['id']}/release", headers=CSRF)
    world.node.sync()  # the host confirms the stopped jobs, freeing its GPUs
    assert world.submit(offer_id=world.offer["id"]).status_code == 201  # rents for the job: not a quota matter


def test_setting_groups_and_quotas_is_admin_only_and_validated(lab):
    path = f"/api/w/{lab.ws}/members/{lab.ids['stu@iit.ac.in']}/compute"
    assert lab.stu.patch(path, headers=CSRF, json={"gpu_hour_quota": 1}).status_code == 403  # no capping yourself
    assert lab.view.patch(path, headers=CSRF, json={"gpu_hour_quota": 1}).status_code == 403
    assert lab.dean.patch(path, headers=CSRF, json={"group": " nlp ", "gpu_hour_quota": 7}).json()["group"] == "nlp"
    for bad in (-1, 1.5, "7", True, 10**7):
        assert lab.dean.patch(path, headers=CSRF, json={"gpu_hour_quota": bad}).status_code == 400, bad
    assert lab.dean.patch(path, headers=CSRF, json={"group": "x" * 65}).status_code == 400
    assert lab.dean.patch(path, headers=CSRF, json={"group": 5}).status_code == 400
    # Only the keys sent change; null clears; an empty group means none.
    assert lab.dean.patch(path, headers=CSRF, json={"group": ""}).json() == {
        "user_id": lab.ids["stu@iit.ac.in"], "group": "", "gpu_hour_quota": 7,
    }
    assert lab.dean.patch(path, headers=CSRF, json={"gpu_hour_quota": None}).json()["gpu_hour_quota"] is None
    assert lab.dean.patch(f"/api/w/{lab.ws}/members/usr_nobody/compute", headers=CSRF, json={}).status_code == 404
    members = {m["email"]: m for m in lab.view.get(f"/api/w/{lab.ws}/members").json()["members"]}
    assert members["stu@iit.ac.in"]["group"] == "" and members["stu@iit.ac.in"]["gpu_hour_quota"] is None

    settings = f"/api/w/{lab.ws}/compute-settings"
    assert lab.stu.patch(settings, headers=CSRF, json={"default_gpu_hour_quota": 5}).status_code == 403
    assert lab.dean.patch(settings, headers=CSRF, json={}).status_code == 400
    assert lab.dean.patch(settings, headers=CSRF, json={"default_gpu_hour_quota": -5}).status_code == 400
    assert lab.dean.patch(settings, headers=CSRF, json={"default_gpu_hour_quota": 40}).status_code == 200
    assert lab.view.get(f"/api/w/{lab.ws}").json()["default_gpu_hour_quota"] == 40
    # A viewer reads usage; someone outside the workspace cannot tell it exists.
    assert lab.view.get(f"/api/w/{lab.ws}/usage").status_code == 200
    outsider = login(lab.client, lab.tmp_path, "x@other.in", lab.clock)
    assert outsider.get(f"/api/w/{lab.ws}/usage").status_code == 404


def test_migration_adds_the_usage_columns_and_matches_the_models(tmp_path):
    pytest.importorskip("alembic")
    from sqlalchemy import create_engine, inspect

    from meridian_control.db import Base, run_migrations

    url = f"sqlite:///{tmp_path}/m.db"
    run_migrations(url)
    insp = inspect(create_engine(url))
    for table, columns in (("memberships", {"group", "gpu_hour_quota"}), ("workspaces", {"default_gpu_hour_quota"})):
        assert columns <= {c["name"] for c in insp.get_columns(table)}
    for name, table in Base.metadata.tables.items():  # every model column exists in the migrated schema
        assert {c.name for c in table.columns} <= {c["name"] for c in insp.get_columns(name)}, name


def test_cli_prints_usage_per_person(lab, monkeypatch):
    from meridian.cli.compute import main

    lab.run(lab.stu, 5400)
    lab.set_compute("stu@iit.ac.in", group="vision-lab", gpu_hour_quota=4)
    made = lab.head.post(f"/api/w/{lab.ws}/api-keys", headers=CSRF, json={"name": "ro", "role": "viewer"})
    token = made.json()["token"]  # a read-only key is enough to report usage
    monkeypatch.setenv("MERIDIAN_WORKSPACE", lab.ws)
    monkeypatch.setenv("MERIDIAN_API_KEY", token)
    lines: list[str] = []
    assert main(["usage", "--month", "2026-08"], client=TestClient(lab.app), out=lines.append) == 0
    assert lines[0] == "usage for 2026-08 (UTC)" and lines[1].split() == [
        "PERSON", "GROUP", "OWN", "GPU-H", "RENTED", "GPU-H", "JOBS", "QUOTA", "LEFT",
    ]
    stu = next(line for line in lines if line.startswith("stu@iit.ac.in"))
    assert stu.split() == ["stu@iit.ac.in", "vision-lab", "1.50", "0.00", "1", "4", "2.50"]
    assert lines[-1].split() == ["TOTAL", "1.50", "0.00", "1"]
    assert main(["usage", "--month", "bad"], client=TestClient(lab.app), out=lines.append) == 1


# ── private sharing between institutes ────────────────────────────────────


def slug_of(c, ws) -> str:
    return c.get(f"/api/w/{ws}").json()["slug"]


def share(world, **body):
    """Edit the world's listing as its provider."""
    return world.provider.patch(f"/api/w/{world.pws}/market/offers/{world.offer['id']}", headers=CSRF, json=body)


def test_a_private_listing_is_visible_and_rentable_only_by_the_workspaces_it_is_shared_with(
    client, tmp_path, clock, world
):
    outsider = login(client, tmp_path, "x@other.in", clock)
    ows = workspace(outsider, "Other Institute")
    world.node.sync()  # the sign-in moved the clock; keep the host's lease alive
    world.credit(ows, 10_000_00, "UTR-0002")
    r = share(world, visibility="private", shared_with=[slug_of(world.consumer, world.cws)])
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "private" and r.json()["shared_with"] == [slug_of(world.consumer, world.cws)]

    seen = world.consumer.get(f"/api/w/{world.cws}/market/offers").json()["offers"]
    assert [o["id"] for o in seen] == [world.offer["id"]] and seen[0]["visibility"] == "private"
    assert "shared_with" not in seen[0] and "node_id" not in seen[0]  # who else is a partner is the owner's business
    assert world.provider.get(f"/api/w/{world.pws}/market/offers").json()["offers"][0]["mine"] is True
    assert outsider.get(f"/api/w/{ows}/market/offers").json()["offers"] == []

    # A workspace that was not invited gets the same 404 as for an id that does not exist, however it asks.
    missing = outsider.post(f"/api/w/{ows}/market/rentals", headers=CSRF, json={"offer_id": "off_nope"})
    hidden = outsider.post(f"/api/w/{ows}/market/rentals", headers=CSRF, json={"offer_id": world.offer["id"]})
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]
    too_big = outsider.post(f"/api/w/{ows}/jobs", headers=CSRF, json={
        "image": IMAGE, "gpus_per_replica": 3, "offer_id": world.offer["id"],
    })
    assert too_big.status_code == 404  # not "TOO_LARGE", which would confirm the listing exists
    shared_too_big = world.submit(gpus_per_replica=3, offer_id=world.offer["id"])
    assert shared_too_big.status_code == 409 and shared_too_big.json()["error"]["code"] == "TOO_LARGE"

    assert world.rent()["offer"]["id"] == world.offer["id"]  # the partner can rent it

    # Sharing it with someone else later does not end the partner's rental, but adds the new partner.
    share(world, shared_with=[slug_of(outsider, ows)])
    assert world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]["status"] == "active"
    assert world.consumer.get(f"/api/w/{world.cws}/market/offers").json()["offers"] == []
    assert [o["id"] for o in outsider.get(f"/api/w/{ows}/market/offers").json()["offers"]] == [world.offer["id"]]


def test_sharing_is_validated_and_making_a_listing_public_again_forgets_the_partners(world):
    partner = slug_of(world.consumer, world.cws)
    r = share(world, visibility="private", shared_with=[partner, "no-such-lab"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "UNKNOWN_WORKSPACE"
    assert "'no-such-lab'" in r.json()["error"]["message"]
    for bad in ({"visibility": "secret"}, {"visibility": "private", "shared_with": "lab"},
                {"visibility": "private", "shared_with": [5]}, {"visibility": "private", "shared_with": ["a"] * 51}):
        assert share(world, **bad).status_code == 400, bad
    assert world.offer["visibility"] == "public" and world.offer["shared_with"] == []

    own = slug_of(world.provider, world.pws)
    ok = share(world, visibility="private", shared_with=[f" {partner.upper()} ", partner, own])
    assert ok.json()["shared_with"] == [partner]  # normalised, de-duplicated, and not including the owner
    back = share(world, visibility="public")
    assert back.json()["visibility"] == "public" and back.json()["shared_with"] == []
    assert len(world.consumer.get(f"/api/w/{world.cws}/market/offers").json()["offers"]) == 1


def test_only_a_private_listing_may_be_free(client, tmp_path, clock, world):
    partner = slug_of(world.consumer, world.cws)
    # Public listings keep the ₹1 minimum, on create and on update.
    assert share(world, price_paise_per_gpu_hour=0).status_code == 400
    assert share(world, visibility="private", shared_with=[partner], price_paise_per_gpu_hour=0).json()[
        "hourly_total_paise"
    ] == 0
    r = share(world, visibility="public")  # now free and public: refused until it has a price
    assert r.status_code == 400 and "at least ₹1" in r.json()["error"]["message"]
    assert share(world, visibility="public", price_paise_per_gpu_hour=100).status_code == 200

    other = FakeNode(client.app, world.provider, world.pws)
    body = {"node_id": other.node_id, "title": "Free for partners", "region": "Pune"}
    free = world.provider.post(f"/api/w/{world.pws}/market/offers", headers=CSRF, json={
        **body, "visibility": "private", "shared_with": [partner], "price_paise_per_gpu_hour": 0,
    })
    assert free.status_code == 201 and free.json()["price_paise_per_gpu_hour"] == 0
    assert free.json()["hourly_total_paise"] == 0 and free.json()["shared_with"] == [partner]
    third = FakeNode(client.app, world.provider, world.pws)
    for extra in ({"price_paise_per_gpu_hour": 0}, {}, {"visibility": "private"}, {"visibility": "public",
                                                                                    "price_paise_per_gpu_hour": 0}):
        r = world.provider.post(f"/api/w/{world.pws}/market/offers", headers=CSRF, json={
            **body, "node_id": third.node_id, **extra,
        })
        assert r.status_code == 400, extra  # a missing price is never silently free


def test_a_free_rental_needs_no_credit_and_posts_no_ledger_rows(client, tmp_path, clock):
    world = World(client, tmp_path, clock, credit=0)  # the renter has no credit at all
    partner = slug_of(world.consumer, world.cws)
    share(world, visibility="private", shared_with=[partner], price_paise_per_gpu_hour=0)
    rental = world.rent()
    assert rental["price_paise_per_gpu_hour"] == 0 and rental["amount_paise"] == 0 and rental["hourly_total_paise"] == 0

    job = world.submit(rental_id=rental["id"], gpus_per_replica=2).json()
    world.node.sync()
    world.tick(3 * 3600)  # three hours: a paid rental with no credit would have been ended long ago
    assert world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]["status"] == "active"
    assert world.consumer.get(f"/api/w/{world.cws}/jobs/{job['id']}").json()["status"] == "running"
    world.tick(10 * 3600)

    done = world.consumer.post(f"/api/w/{world.cws}/market/rentals/{rental['id']}/release", headers=CSRF).json()
    assert done["status"] == "ended" and done["amount_paise"] == 0 and done["seconds"] == 13 * 3600
    for ws, c in ((world.cws, world.consumer), (world.pws, world.provider)):
        b = world.balance(c, ws)
        assert b["balance_paise"] == 0 and b["entries"] == [] and b["burn_paise_per_hour"] == 0
    with world.app.state.compute._sf() as s:
        assert s.scalar(select(func.count()).select_from(LedgerEntry)) == 0  # not even a zero-amount row


def test_a_free_rental_survives_while_a_paid_one_runs_out_of_credit(client, tmp_path, clock):
    world = World(client, tmp_path, clock, credit=300_00)  # a little over an hour of the paid listing
    paid_offer = world.offer
    free_node = FakeNode(client.app, world.provider, world.pws, name="free-host")
    partner = slug_of(world.consumer, world.cws)
    free = world.provider.post(f"/api/w/{world.pws}/market/offers", headers=CSRF, json={
        "node_id": free_node.node_id, "title": "Free for partners", "region": "Pune", "visibility": "private",
        "shared_with": [partner], "price_paise_per_gpu_hour": 0,
    }).json()
    free_rental = world.consumer.post(f"/api/w/{world.cws}/market/rentals", headers=CSRF, json={"offer_id": free["id"]})
    paid_rental = world.consumer.post(
        f"/api/w/{world.cws}/market/rentals", headers=CSRF, json={"offer_id": paid_offer["id"]}
    )
    assert free_rental.status_code == paid_rental.status_code == 201
    for _ in range(3):
        world.clock.advance(3600)
        free_node.sync()  # both hosts keep reporting
        world.tick()
    rentals = {r["id"]: r for r in world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"]}
    assert rentals[paid_rental.json()["id"]]["end_reason"] == "insufficient_funds"
    assert rentals[free_rental.json()["id"]]["status"] == "active"
    entries = world.balance(world.consumer, world.cws)["entries"]
    assert entries and all(e["amount_paise"] != 0 for e in entries)  # no zero-amount rows


def test_cli_lists_a_free_private_listing_as_free(world, monkeypatch):
    from meridian.cli.compute import main

    share(world, visibility="private", shared_with=[slug_of(world.consumer, world.cws)], price_paise_per_gpu_hour=0)
    key = world.consumer.post(f"/api/w/{world.cws}/api-keys", headers=CSRF, json={"name": "cli", "role": "viewer"})
    monkeypatch.setenv("MERIDIAN_WORKSPACE", world.cws)
    monkeypatch.setenv("MERIDIAN_API_KEY", key.json()["token"])
    lines: list[str] = []
    assert main(["offers"], client=TestClient(world.app), out=lines.append) == 0
    assert lines[1].split()[-6:] == ["Mumbai", "free", "free", "available", "Mumbai", "DC"]


def test_migration_adds_listing_visibility_with_public_as_the_default(tmp_path):
    pytest.importorskip("alembic")
    from sqlalchemy import create_engine, inspect, text

    from meridian_control.db import run_migrations

    url = f"sqlite:///{tmp_path}/m.db"
    run_migrations(url)
    cols = {c["name"]: c for c in inspect(create_engine(url)).get_columns("market_offers")}
    assert {"visibility", "shared_with"} <= set(cols)
    with create_engine(url).begin() as conn:  # a row written without the new columns is public and shared with no one
        conn.execute(text(
            "INSERT INTO market_offers (id, workspace_id, node_id, title, description, region, gpu_model, gpu_count,"
            " vram_bytes_per_gpu, device_ids, price_paise_per_gpu_hour, status, created_by, created_at, updated_at)"
            " VALUES ('o','w','n','t','','r','m',1,0,'[]',100,'listed','','2026-01-01','2026-01-01')"
        ))
        assert conn.execute(text("SELECT visibility, shared_with FROM market_offers")).one() == ("public", "[]")


# ── public listings feed ──────────────────────────────────────────────────

OFFER_KEYS = {
    "title", "region", "gpu_model", "gpu_count", "vram_bytes_per_gpu", "price_paise_per_gpu_hour",
    "hourly_total_paise", "available", "provider_name",
}


def feed(app):
    """The public feed, fetched with no cookie and no credentials."""
    return TestClient(app).get("/api/public/offers")


def test_public_feed_needs_no_sign_in_and_is_the_only_cacheable_api_route(world):
    r = feed(world.app)
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=30"
    body = r.json()
    assert len(body["offers"]) == 1 and set(body["offers"][0]) == OFFER_KEYS
    assert body["offers"][0] == {
        "title": "2× H100, Mumbai", "region": "Mumbai", "gpu_model": "NVIDIA H100", "gpu_count": 2,
        "vram_bytes_per_gpu": 80 * GB, "price_paise_per_gpu_hour": 100_00, "hourly_total_paise": 204_00,
        "available": True, "provider_name": "Mumbai DC",
    }
    assert body["summary"] == {
        "available_gpus": 2, "gpus_by_model": {"NVIDIA H100": 2}, "regions": ["Mumbai"],
        "lowest_price_paise_per_gpu_hour": 100_00,
    }
    # Nothing that identifies a node, a device or a workspace leaks into the page.
    for secret in (world.node.node_id, world.pws, world.cws, world.offer["id"], world.node.devices[0], "mine"):
        assert secret not in r.text
    # Every other /api response is still no-store.
    anon = TestClient(world.app)
    assert anon.get("/api/cloud/info").headers["cache-control"] == "no-store"
    assert anon.get("/api/me").headers["cache-control"] == "no-store"


def test_public_feed_never_shows_private_paused_or_retired_listings_and_marks_rented_ones_unavailable(
    client, tmp_path, clock, world
):
    partner = slug_of(world.consumer, world.cws)
    second = FakeNode(client.app, world.provider, world.pws, gpus=4, model="NVIDIA A100", vram_gb=40, name="a100")
    made = world.provider.post(f"/api/w/{world.pws}/market/offers", headers=CSRF, json={
        "node_id": second.node_id, "title": "4× A100, Pune", "region": "Pune", "price_paise_per_gpu_hour": 50_00,
    })
    assert made.status_code == 201, made.text
    hidden = {}
    for name, extra in (("private", {"visibility": "private", "shared_with": [partner]}), ("paused", {})):
        node = FakeNode(client.app, world.provider, world.pws, name=name)
        r = world.provider.post(f"/api/w/{world.pws}/market/offers", headers=CSRF, json={
            "node_id": node.node_id, "title": f"{name} listing", "region": "Delhi", "price_paise_per_gpu_hour": 10_00,
            **extra,
        })
        assert r.status_code == 201, r.text
        hidden[name] = r.json()
    world.provider.patch(f"/api/w/{world.pws}/market/offers/{hidden['paused']['id']}", headers=CSRF,
                         json={"status": "paused"})
    retired = FakeNode(client.app, world.provider, world.pws, name="retired")
    r = world.provider.post(f"/api/w/{world.pws}/market/offers", headers=CSRF, json={
        "node_id": retired.node_id, "title": "retired listing", "region": "Delhi", "price_paise_per_gpu_hour": 10_00,
    })
    world.provider.delete(f"/api/w/{world.pws}/market/offers/{r.json()['id']}", headers=CSRF)
    clock.advance(31)  # past the feed's cache
    world.node.sync()
    second.sync()

    body = feed(world.app).json()
    assert [o["title"] for o in body["offers"]] == ["4× A100, Pune", "2× H100, Mumbai"]  # cheapest first
    assert body["summary"] == {
        "available_gpus": 6, "gpus_by_model": {"NVIDIA A100": 4, "NVIDIA H100": 2}, "regions": ["Mumbai", "Pune"],
        "lowest_price_paise_per_gpu_hour": 50_00,
    }
    assert "private listing" not in feed(world.app).text and "Delhi" not in feed(world.app).text

    # A rented listing stays on the page, marked unavailable, and leaves the summary.
    world.rent()
    clock.advance(31)
    world.node.sync()
    second.sync()
    body = feed(world.app).json()
    shown = [(o["title"], o["available"]) for o in body["offers"]]
    assert shown == [("4× A100, Pune", True), ("2× H100, Mumbai", False)]
    assert body["summary"]["available_gpus"] == 4 and body["summary"]["gpus_by_model"] == {"NVIDIA A100": 4}
    assert body["summary"]["regions"] == ["Pune"]


def test_public_feed_is_cached_for_thirty_seconds(world, clock):
    first = feed(world.app).json()
    world.provider.patch(f"/api/w/{world.pws}/market/offers/{world.offer['id']}", headers=CSRF,
                         json={"price_paise_per_gpu_hour": 50_00})
    clock.advance(29)
    assert feed(world.app).json() == first  # still the stored copy
    clock.advance(2)
    world.node.sync()
    fresh = feed(world.app).json()
    assert fresh["offers"][0]["price_paise_per_gpu_hour"] == 50_00 and fresh != first


def test_public_feed_with_nothing_listed_is_empty_not_an_error(client, tmp_path, clock):
    body = feed(client.app).json()
    assert body == {"offers": [], "summary": {
        "available_gpus": 0, "gpus_by_model": {}, "regions": [], "lowest_price_paise_per_gpu_hour": None,
    }}


def test_public_feed_exists_only_in_cloud_mode(tmp_path):
    from meridian_control.app import create_app
    from meridian_control.config import ControlConfig

    app = create_app(ControlConfig(db_url=f"sqlite:///{tmp_path}/standalone.db", ca_dir=tmp_path / "ca", cloud=False))
    assert TestClient(app).get("/api/public/offers").status_code == 404


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_rents_runs_follows_and_reports_the_bill(world, monkeypatch, capsys):
    from meridian.cli.compute import main, rupees

    assert rupees(204_00) == "₹204.00" and rupees(-12_345_05) == "-₹12,345.05" and rupees(5) == "₹0.05"
    key = world.consumer.post(
        f"/api/w/{world.cws}/api-keys", headers=CSRF, json={"name": "laptop", "role": "admin"}
    ).json()["token"]
    monkeypatch.setenv("MERIDIAN_WORKSPACE", world.cws)
    monkeypatch.setenv("MERIDIAN_API_KEY", key)
    http = TestClient(world.app)
    lines: list[str] = []

    def run(*argv, sleep=lambda _s: None):
        lines.clear()
        return main(list(argv), client=http, out=lines.append, sleep=sleep)

    assert run("offers") == 0
    assert lines[0].split()[:2] == ["ID", "GPUS"]
    assert world.offer["id"] in lines[1] and "₹204.00" in lines[1] and "available" in lines[1]

    def node_finishes(_seconds):  # the node picks the job up, runs it, exits 0
        desired = world.node.sync()
        if desired.get("jobs") and world.node.phases[(desired["jobs"][0]["job_id"], 0)]["phase"] == "Running":
            world.node.finish(desired["jobs"][0]["job_id"], 0, logs="accuracy 0.93\n")

    code = run("run", "--offer", world.offer["id"], "--image", IMAGE, "--gpus", "2", "-e", "SEED=7", "--follow",
               "--", "python", "eval.py", sleep=node_finishes)
    assert code == 0
    assert lines[0].startswith("submitted job_") and "succeeded" in "\n".join(lines) and lines[-1] == "accuracy 0.93"

    assert run("rentals") == 0 and "ended (job_finished)" in lines[1]
    assert run("jobs") == 0 and "succeeded" in lines[1]
    assert run("balance") == 0 and lines[0].startswith("balance:")

    assert run("rent", "off_missing") == 1
    assert "listing not found (HTTP 404)" in capsys.readouterr().err
    monkeypatch.delenv("MERIDIAN_API_KEY")
    assert run("offers") == 2 and "MERIDIAN_API_KEY" in capsys.readouterr().err
