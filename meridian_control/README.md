# meridian-control

The central control plane for [`meridian-node`](https://github.com/IMV-IN/meridian-node)
GPU agents. It is a **separate service** from the Meridian gateway (it does not
import or modify gateway code); the gateway consumes its serving projection
read-only.

It implements the node control protocol (`contracts/v1` in the meridian-node
repo): approval-gated enrollment with a built-in Ed25519 CA, epoch-fenced
sessions with restore-safe fencing, leases, desired-state generations, and
observations. State is durable via SQLAlchemy — **SQLite by default**, Postgres
via a connection URL — so control replicas are stateless.

See the meridian-node `DESIGN.md`, decision 13 and sections 8, 10, 15, and 17.

## Install & run

```bash
pip install -e ".[control]"          # from the Meridian repo root

# Run the control plane (SQLite by default)
meridian-control run --host 0.0.0.0 --port 8443
# Or point at Postgres (apply the schema with Alembic first):
MERIDIAN_CONTROL_DB_URL=postgresql+psycopg://user:pass@host/meridian_control \
  meridian-control migrate
MERIDIAN_CONTROL_DB_URL=postgresql+psycopg://user:pass@host/meridian_control \
  meridian-control run

# Mint a one-time enrollment token for a node
meridian-control mint-token --auto-approve
```

Production manages the schema with **Alembic** (`meridian-control migrate`, or
`alembic -c meridian_control/alembic.ini upgrade head`). `create_all` remains the
zero-config default for local dev and tests.

Container image (separate from the gateway image): `deploy/control/Dockerfile`.
See [docs/CONTROL_PLANE.md](../docs/CONTROL_PLANE.md) for the full deploy flow and
the production-readiness checklist.

## Endpoints

| Method + path | Purpose |
|---|---|
| `POST /control/v1/enroll` | Node enrollment (Bearer token) |
| `GET /control/v1/enroll/claims/{id}` | Pending-approval possession handshake |
| `POST /control/v1/nodes/{id}/sessions` | Establish a fenced session |
| `POST /control/v1/nodes/{id}/heartbeat` | Renew lease, get desired generation |
| `GET /control/v1/nodes/{id}/desired-state` | Fetch the desired snapshot |
| `POST /control/v1/nodes/{id}/observations` | Report observed state |
| `POST /control/v1/nodes/{id}/certificate` | Rotate the node certificate (mTLS-gated) |
| `POST /admin/tokens` · `/admin/claims/{id}/approve` · `/admin/nodes/{id}/desired` · `/admin/nodes/{id}/stop-authorize` · `/admin/nodes/{id}/revoke` · `/admin/restore` | Operator actions |
| `GET /admin/projection` | Serving projection (gateway consumes read-only) |
| `POST /admin/place` | Capacity-aware placement: pick a node/device with enough allocatable VRAM |
| `GET /admin/crl` | Certificate revocation list (revoked node ids + cert serials) for the edge |

## Restore safety

`POST /admin/restore {"high_water_epoch": N}` is the restore-from-backup runbook
step (DESIGN 17.5): it increments the control-plane incarnation and raises the
epoch floor past the last-issued high-water mark, so a fencing epoch that
regressed in restored data can never re-admit a previously fenced agent.

## Tests

```bash
pytest tests/control
```

`tests/control/test_cross_repo.py` and `test_cross_repo_gateway.py` run the
**real meridian-node agent** against this service over a real HTTP socket — the
cross-repository compatibility target from the node's DESIGN §20/§24. They are
skipped unless `meridian-node` is installed (`pip install -e ../meridian-node[http]`).

### End-to-end connection (with numbers)

`scripts/verify_connection.py` drives the full `node ↔ control ↔ gateway` path
over a real socket with mTLS enforced (`require_mtls=True`) and prints measured
latencies/counts: enrollment, the mTLS gate (no-cert rejected, cert accepted),
50 heartbeats, desired publish/fetch, a Ready-engine observation, the serving
projection, gateway backend registration via `ManagedProjectionSync`, and
certificate rotation. `test_cross_repo_gateway.py` asserts the same chain.

```bash
python scripts/verify_connection.py   # prints a numbers report, exits 0 on PASS
```

## Not yet included

- Certificate **revocation** automation / CRL distribution (rotation is
  implemented: `POST /control/v1/nodes/{id}/certificate`).
