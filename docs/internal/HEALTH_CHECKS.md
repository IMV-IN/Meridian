# Health Checking & Failover

How Meridian decides a backend is healthy, how failover behaves, and how to
tune it. Source: `meridian/health/checker.py`, `meridian/registry/backend.py`.

---

## Two signals, one verdict

Meridian combines **active** and **passive** health signals into a single
per-backend healthy/unhealthy state. Only healthy backends are eligible for
routing; telemetry (queue depth, GPU memory) never gates eligibility — it only
tilts preference among healthy backends.

### Active checks (background task)

Every `interval_s` seconds, the checker GETs each backend's
`health_endpoint` (default `/v1/models`) with a `timeout_s` deadline:

- **HTTP status < 500** → counts as a success (4xx is "alive but unhappy";
  the backend is up even if the path needs auth).
- **HTTP 5xx, timeout, or connection error** → counts as a failure.

All backends are checked concurrently each round; one slow backend does not
delay checks on the others.

### Passive checks (request path)

When a proxied request hits a connection error or 5xx from the backend, the
request path calls `check_passive_failure()`, which records a failure exactly
like an active probe. This means a broken backend under real traffic is
detected **faster than the active interval** — failures accrue from live
requests between probes.

### Thresholds (debounce)

State flips are debounced by consecutive-count thresholds:

- `fail_threshold` (default **2**) consecutive failures → **unhealthy**
- `success_threshold` (default **1**) consecutive success → **healthy**

A single blip does not eject a backend; a recovered backend returns quickly.

## Failover behavior

- Unhealthy backends are removed from the candidate set for every strategy.
- If a request's tier (workload tiering) has no healthy backend, Meridian
  falls back to **all healthy backends** — reliability over isolation.
- Session affinity: if a pinned backend goes unhealthy, the session is
  remapped to another healthy backend and re-pinned.
- If **no** backend is healthy, requests fail with 503.

## Configuration

```yaml
health:
  interval_s: 5        # seconds between active check rounds
  timeout_s: 2         # per-probe deadline
  fail_threshold: 2    # consecutive failures -> unhealthy
  success_threshold: 1 # consecutive successes -> healthy

backends:
  - name: "fast"
    url: "http://backend-fast:9001"
    health_endpoint: "/v1/models"   # per-backend probe path
```

### Detection-time math

Worst-case active detection = `interval_s × fail_threshold` (+ up to
`timeout_s` per probe). Defaults: a dead backend is ejected within ~10–14 s,
sooner under live traffic thanks to passive failures. Recovery: within
~`interval_s` after the backend responds again.

### Tuning guidance

| Goal | Change | Trade-off |
|---|---|---|
| Faster ejection | lower `interval_s` and/or `fail_threshold: 1` | flappier under transient blips |
| Fewer false ejections (overloaded-but-alive backends) | raise `fail_threshold` to 3 | slower failover |
| Slow backends timing out probes | raise `timeout_s` | slower rounds; a genuinely hung backend takes longer to fail |
| Cautious re-admission after crashes | `success_threshold: 2–3` | recovered capacity sits idle longer |

Rule of thumb: probe endpoint should be **cheap** on the backend. `/v1/models`
is fine for vLLM/Ollama; avoid endpoints that trigger model work.

## Observability

- **Prometheus:** `meridian_backend_healthy{backend=...}` gauge (1/0), plus
  inflight and latency series per backend.
- **Status endpoint:** `GET /meridian/status` — health, consecutive counters,
  inflight, EWMA latency per backend.
- **Dashboard:** `/ui` shows live health with ~1 s polling.
- **Logs:** `meridian.health` logger warns on each probe failure with the
  failure type.

Suggested alert: `meridian_backend_healthy == 0` for > 1m per backend, and
`sum(meridian_backend_healthy) == 0` (page — no capacity) immediately.

## Validation recipe

From the compose demo (README "Failover demo"):

```bash
docker compose up --build
docker stop meridian-v1-backend-fast-1     # kill a backend
# within ~10s: /meridian/status shows fast unhealthy; traffic shifts to slow
docker start meridian-v1-backend-fast-1    # within ~5s: healthy again
```

## Current limitations

- Health state is **per-gateway-process** (in-memory); multiple gateway
  replicas each learn independently. Fine for the intended 1–2 node scale.
- Probe is a plain GET — no auth header support for backends whose health
  endpoint requires a key (workaround: use an unauthenticated endpoint).
- Container-level `HEALTHCHECK` for Meridian itself is missing from the
  Dockerfile — tracked in [`KNOWN_ISSUES.md`](../KNOWN_ISSUES.md) §5, fixed in
  Milestone K.
