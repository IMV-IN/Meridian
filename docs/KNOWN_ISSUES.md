# Known Issues

Findings from the 2026-07-02 codebase audit. Closed items stay listed briefly
for history; open items still block or shape the path to v1.0.

_Last updated: 2026-07-09 (Milestone K)._

---

## Closed in Milestone J / K

| # | Issue | Closed in |
|---|---|---|
| 1 | Unbounded rate-limit bucket map | **K** — `RateLimitStore` idle TTL + max keys |
| 2 | Audit / inflight loss on mid-stream cancel | **K** — sync `_finalize_request` + `enqueue` |
| 3 | Rate-limit token spent before model-access 403 | **J** — access → budget → rate-limit order |
| 4 | Version/tag drift (`pyproject` stuck at 0.1.0) | **K** — package at 0.6.0; CI + release checks |
| 5 | Container root / no HEALTHCHECK | **K** — non-root USER + HEALTHCHECK |
| 6 | No request body size cap | **K** — `gateway.max_body_bytes` → 413 |

---

## Open (low / opportunistic)

### 7. Global module-level state (design smell)
- **Where:** `meridian/api/main.py` module globals
- **What:** `_registry`, `_strategy`, `_config`, etc. mutated in `init_app()`.
  Safe under single-lifespan; fragile for multi-app embedding.
- **Fix:** fold into `app.state` when next touching the file; not a dedicated milestone.

### 8. Session eviction heuristic
- **Where:** `meridian/router/affinity.py`
- **What:** at `max_sessions`, nearest-expiry entry is evicted (documented in
  SessionStore docstring / K comments on RateLimitStore). No code change required.

---

## Test gaps (remaining)

- Full soak: 1M requests from many IPs with RSS flat (manual / load recipe for K DoD).
- Passive failure coverage from request-path 5xx remains thin.
- No concurrency race tests for `Backend` counters under simultaneous health + traffic.
