# Control plane & GPU node agents

Meridian has two planes that work together:

- **The gateway** (this repo, the `meridian` package) — the request/traffic plane.
  It decides *whether* a backend may receive traffic and routes to it.
- **The control plane** (`meridian-control`, in this repo) + **node agents**
  ([`meridian-node`](https://github.com/IMV-IN/meridian-node)) — the fleet plane.
  Central `meridian-control` decides *what* should run on each GPU host; the
  `meridian-node` agent decides *how* to run it safely on that host.

> Central Meridian decides **what** should run and **whether** it may receive
> traffic. `meridian-node` decides **how** to make it run safely on one GPU host.

```
 meridian-node agent  ──enroll / mTLS / heartbeat / desired-state / observations──▶  meridian-control
        (GPU host)                                                                        (this repo)
                                                                                             │
 gateway  ◀──── GET /admin/projection (routable managed engines) ────────────────────────────┘
```

## meridian-control

A **separate deployable** from the gateway (it does not import or modify gateway
code). It implements the node control protocol (`contracts/v1` in the
meridian-node repo): approval-gated enrollment with a built-in Ed25519 CA,
epoch-fenced sessions with restore-safe fencing, leases, desired-state
generations, observations, certificate rotation and revocation/CRL, and a
capacity-aware placement selector. State is durable via SQLAlchemy — **SQLite by
default, Postgres via a URL** — so control replicas are stateless.

See [`meridian_control/README.md`](https://github.com/IMV-IN/Meridian/blob/main/meridian_control/README.md)
for the full endpoint list and the meridian-node `DESIGN.md` for the protocol.

## Deploy

`meridian-control` ships its own image (`deploy/control/Dockerfile`), separate
from the gateway image.

```bash
docker build -f deploy/control/Dockerfile -t meridian-control .

# 1. Apply the schema (Alembic) — run once per DB / upgrade.
docker run --rm \
  -e MERIDIAN_CONTROL_DB_URL=postgresql+psycopg://user:pass@db/meridian_control \
  meridian-control migrate

# 2. Serve. Persist the CA directory; enable mTLS in production.
docker run -d --name meridian-control -p 8443:8443 \
  -e MERIDIAN_CONTROL_DB_URL=postgresql+psycopg://user:pass@db/meridian_control \
  -e MERIDIAN_CONTROL_REQUIRE_MTLS=1 \
  -v meridian-ca:/var/lib/meridian-control/ca \
  meridian-control run --host 0.0.0.0 --port 8443

# 3. Mint a one-time enrollment token for a node.
docker exec meridian-control meridian-control mint-token --auto-approve
```

The gateway consumes `GET /admin/projection` read-only (set `control_plane.url`
in the gateway config; see the projection-sync notes). Node agents are installed
from the [meridian-node](https://github.com/IMV-IN/meridian-node) repo
(systemd units + Dockerfile + example configs in its `deploy/`).

## Production readiness checklist

The system is feature-complete and tested (gateway 540, control 41, node 95 unit
tests + real-GPU host tests on an RTX 4060). Before a production rollout:

- [ ] **Enable mTLS.** `MERIDIAN_CONTROL_REQUIRE_MTLS=1`, terminate mTLS at the
      edge, and forward the verified client cert in `x-client-cert`. The app must
      be reachable **only** through that edge. (Default is off for dev.)
- [ ] **Use Postgres** (`MERIDIAN_CONTROL_DB_URL`), run `meridian-control migrate`
      on deploy, and tune the pool (`MERIDIAN_CONTROL_DB_POOL_SIZE` /
      `_MAX_OVERFLOW`).
- [ ] **Persist and back up the CA directory** (`/var/lib/meridian-control/ca`).
      Follow the restore runbook (`POST /admin/restore`) after any DB restore so
      fencing epochs cannot regress.
- [ ] **Node hosts:** deploy the root helper, pin allowed image digests + trusted
      signing keys + per-device capacity in the root-owned policy, and start in
      `observe-only` before enabling `managed-host`.
- [ ] **Validate the newer engine drivers** (TGI, SGLang, llama.cpp, LMDeploy)
      against real engine images on a host — their launch flags are typed and
      unit-tested but not yet run against live engines.

## What's left (deliberately deferred)

**Load-driven batched observation writes** beyond ~500 nodes (meridian-node
DESIGN §10.7). Batching trades projection/placement read-freshness for write
throughput, so it should follow a measured load profile rather than a guess.
Connection pooling is the correct first lever and is in place.
