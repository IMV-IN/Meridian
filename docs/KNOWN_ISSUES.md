# Known Issues

Findings from the 2026-07-02 codebase audit. Closed items stay listed briefly
for history; open items still block or shape the path to v1.0.

_Last updated: 2026-07-10 (v1.0 gate)._

---

## Closed (J–N / 0.9.x)

| # | Issue | Closed in |
|---|---|---|
| 1 | Unbounded rate-limit bucket map | **K** — `RateLimitStore` idle TTL + max keys |
| 2 | Audit / inflight loss on mid-stream cancel | **K** — sync finalize + enqueue |
| 3 | Rate-limit token spent before model-access 403 | **J** — access → budget → rate-limit order |
| 4 | Version/tag drift | **K+** — package + CI version check; current **0.9.3** |
| 5 | Container root / no HEALTHCHECK | **K** — non-root USER + HEALTHCHECK |
| 6 | No request body size cap | **K** — `gateway.max_body_bytes` → 413 |
| 7 | Module-level app globals | **refactor** — `AppState` + pipeline |
| 9 | Client Auth forwarded upstream | **refactor** — never forward; optional `auth_header` |

---

## Open (low / opportunistic — not v1.0 blockers)

### 8. Session eviction heuristic
- **Where:** `meridian/router/affinity.py`
- **What:** at `max_sessions`, nearest-expiry entry is evicted (documented).

### 10. Multi-replica shared budget/cost state
- Single-process sqlite/memory. External Redis/store is post-v1.0 if needed.

---

## Test gaps (remaining)

- Passive failure coverage from request-path 5xx remains thin.
- No concurrency race tests for `Backend` counters under simultaneous health + traffic.

## Manual validation recipes

### Rate-limit store bound (K leftover)

With `rate_limit.enabled: true` and auth off, generate traffic from many
distinct `X-Forwarded-For` values. After idle TTL, `RateLimitStore.sweep()`
(or wait for the background sweep) should drop idle keys so RSS does not
grow without bound. Unit coverage lives in `tests/test_rate_limit_store.py`.
