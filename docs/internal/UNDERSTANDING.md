# Meridian — Complete Codebase Walkthrough

> Everything you need to know about how Meridian works, from request entry to response exit.

---

## Table of Contents

1. [What Meridian Is](#1-what-meridian-is)
2. [Project Structure](#2-project-structure)
3. [Config Layer](#3-config-layer)
4. [Registry — Backend State](#4-registry--backend-state)
5. [Token Estimator](#5-token-estimator)
6. [Routing Strategies](#6-routing-strategies)
7. [Health Checker](#7-health-checker)
8. [Proxy — Forwarding Requests](#8-proxy--forwarding-requests)
9. [Telemetry System](#9-telemetry-system)
10. [Metrics — Prometheus](#10-metrics--prometheus)
11. [Audit Logging — JSONL](#11-audit-logging--jsonl)
12. [API Layer — The Orchestrator](#12-api-layer--the-orchestrator)
13. [Mock Backend](#13-mock-backend)
14. [Live Dashboard — UI](#14-live-dashboard--ui)
15. [CLI Entry Point](#15-cli-entry-point)
16. [Tests](#16-tests)
17. [Docker Setup](#17-docker-setup)
18. [Request Flow — End to End](#18-request-flow--end-to-end)
19. [Data Flow Diagram](#19-data-flow-diagram)
20. [Running Instances](#20-running-instances)
21. [Connecting to OpenAI SDK](#21-connecting-to-openai-sdk)

---

## 1. What Meridian Is

Meridian is an **OpenAI-compatible inference gateway**. It sits between your application and multiple LLM backends (vLLM, SGLang, Ollama, etc.) and provides:

- **Routing** — picks which backend handles each request
- **Health checking** — automatically removes broken backends from rotation
- **Failover** — if a backend fails, traffic shifts to healthy ones
- **Observability** — Prometheus metrics, JSONL logs, live dashboard
- **Streaming passthrough** — forwards SSE tokens without buffering

It does **not**:
- Run any models itself
- Validate API keys (yet)
- Store any data permanently
- Do PII detection or redaction

---

## 2. Project Structure

```
Meridian/
├── meridian/                    # Main package (the gateway)
│   ├── __init__.py              # Version: 0.1.0
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py              # FastAPI app — the entrypoint
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py              # CLI: `meridian serve -c config.yaml`
│   ├── config/
│   │   ├── __init__.py
│   │   └── models.py            # Pydantic config schema
│   ├── health/
│   │   ├── __init__.py
│   │   └── checker.py           # Active health checker
│   ├── metrics/
│   │   ├── __init__.py
│   │   ├── collectors.py        # Prometheus gauges/counters/histograms
│   │   └── logger.py            # JSONL request logger
│   ├── proxy/
│   │   ├── __init__.py
│   │   └── forward.py           # HTTPX forwarding (stream + non-stream)
│   ├── registry/
│   │   ├── __init__.py
│   │   └── backend.py           # Backend state + registry
│   ├── router/
│   │   ├── __init__.py
│   │   ├── strategies.py        # 4 routing strategies
│   │   └── token_estimator.py   # Heuristic token counter
│   ├── telemetry/
│   │   ├── __init__.py
│   │   ├── base.py              # ABC + dataclass
│   │   ├── json_adapter.py      # JSON-over-HTTP adapter
│   │   └── poller.py            # Background telemetry poller
│   ├── ui/
│   │   └── index.html           # Live dashboard (vanilla JS)
│   └── util/
│       ├── __init__.py
│       └── helpers.py           # Request ID generation, timing
├── tests/                       # Test suite
│   ├── test_api_integration.py  # E2E with mock backend
│   ├── test_config.py           # Config loading
│   ├── test_registry.py         # Backend registry
│   ├── test_router.py           # All 4 strategies
│   ├── test_token_estimator.py  # Token counting
│   ├── test_telemetry_json.py   # JSON adapter parsing
│   └── test_telemetry_poller.py # Poller invariant
├── mock_backend/
│   └── server.py                # Fake OpenAI server for demos
├── configs/
│   ├── mock_demo.yaml           # Mock backends (Docker)
│   ├── local_gpu.yaml           # Single GPU (Ollama)
│   └── dual_backend.yaml        # Failover testing
├── scripts/
│   └── smoke_test.py            # E2E smoke test
├── config.yaml                  # Default config
├── docker-compose.yml           # 3 services (gateway + 2 backends)
├── Dockerfile                   # Single-stage Python build
├── pyproject.toml               # Dependencies + tooling config
└── CLAUDE.md                    # Development rules
```

---

## 3. Config Layer

**File:** `meridian/config/models.py`

Everything starts here. Pydantic models define the entire system configuration. No raw dicts anywhere — every module receives typed objects.

### Config Hierarchy

```
MeridianConfig
├── GatewayConfig
│   ├── host: str = "0.0.0.0"
│   ├── port: int = 8080
│   ├── strategy: str = "least_inflight"
│   ├── prefill_weight: float = 1.0       # token_aware only
│   ├── decode_weight: float = 4.0        # token_aware only
│   ├── default_max_tokens: int = 256     # token_aware only
│   ├── token_estimator: str = "heuristic"
│   ├── queue_weight: float = 0.0         # capacity penalty
│   └── mem_weight: float = 0.0           # capacity penalty
├── HealthConfig
│   ├── interval_s: float = 5.0
│   ├── timeout_s: float = 2.0
│   ├── fail_threshold: int = 2
│   └── success_threshold: int = 1
├── LoggingConfig
│   ├── level: str = "INFO"
│   └── jsonl_path: str = "./meridian_requests.jsonl"
└── List[BackendConfig]
    ├── name: str
    ├── url: str
    ├── engine: str = "vllm"
    ├── model: str = ""
    ├── weight: int = 1
    ├── tags: List[str] = []
    ├── health_endpoint: str = "/v1/models"
    └── telemetry: Optional[BackendTelemetryConfig]
        ├── type: str = "json"
        ├── url: str
        ├── interval_s: float = 5.0
        └── timeout_s: float = 2.0
```

### How Config is Loaded

In `meridian/api/main.py:64-68`:

```python
def _load_config() -> MeridianConfig:
    path = os.environ.get("MERIDIAN_CONFIG", "config.yaml")
    if os.path.exists(path):
        return MeridianConfig.from_yaml(path)
    return MeridianConfig()
```

- Reads `MERIDIAN_CONFIG` env var (defaults to `config.yaml`)
- Parses YAML, validates with Pydantic
- Returns typed `MeridianConfig` object

### Example Config (config.yaml)

```yaml
gateway:
  host: "0.0.0.0"
  port: 8080
  strategy: "least_inflight"

health:
  interval_s: 5
  timeout_s: 2
  fail_threshold: 2
  success_threshold: 1

logging:
  level: "INFO"
  jsonl_path: "./meridian_requests.jsonl"

backends:
  - name: "fast"
    url: "http://backend-fast:9001"
    engine: "mock"
    model: "demo-model"
    weight: 80
    tags: ["fast"]
    health_endpoint: "/v1/models"

  - name: "slow"
    url: "http://backend-slow:9002"
    engine: "mock"
    model: "demo-model"
    weight: 20
    tags: ["cheap"]
    health_endpoint: "/v1/models"
```

---

## 4. Registry — Backend State

**File:** `meridian/registry/backend.py`

Two classes: `Backend` (one inference endpoint) and `BackendRegistry` (all backends).

### Backend Class

Each backend tracks:

| Field | Type | Purpose |
|-------|------|---------|
| `healthy` | bool | Is this backend accepting traffic? |
| `inflight` | int | How many requests are currently being processed? |
| `inflight_cost` | float | Sum of cost for all inflight requests |
| `ewma_latency_ms` | float | Exponential moving average of request latency |
| `queue_depth` | Optional[int] | Telemetry signal: how many requests are queued |
| `tokens_per_sec` | Optional[float] | Telemetry signal: throughput |
| `gpu_mem_util` | Optional[float] | Telemetry signal: GPU memory usage (0.0-1.0) |

All mutable state is protected by `threading.Lock` for thread safety.

### Thread Safety Pattern

Every mutation follows this pattern:

```python
def increment_inflight(self) -> None:
    with self._lock:
        self.inflight += 1
```

### EWMA Latency Calculation

```python
def update_latency(self, latency_ms: float) -> None:
    with self._lock:
        if self.ewma_latency_ms == 0.0:
            self.ewma_latency_ms = latency_ms
        else:
            self.ewma_latency_ms = (
                self._ewma_alpha * latency_ms
                + (1 - self._ewma_alpha) * self.ewma_latency_ms
            )
```

Alpha = 0.3 means 30% weight to new measurement, 70% to history.

### BackendRegistry

```python
class BackendRegistry:
    def eligible(self, model: str, tags: Optional[Set[str]] = None) -> List[Backend]:
        """Return healthy backends matching model and tags."""
        result = []
        for b in self.backends:
            if not b.healthy:           # Skip unhealthy
                continue
            if b.model and b.model != model:  # Skip wrong model
                continue
            if tags and not tags.issubset(b.tags):  # Skip missing tags
                continue
            result.append(b)
        return result
```

Three filters applied in order: health → model → tags.

### Health State Machine

```
                success_threshold reached
    Unhealthy ──────────────────────────> Healthy
        ^                                      │
        │          fail_threshold reached      │
        └──────────────────────────────────────┘
```

- `record_health_success(threshold)`: increments consecutive successes, resets failures, marks healthy when threshold met
- `record_health_failure(threshold)`: increments consecutive failures, resets successes, marks unhealthy when threshold met

---

## 5. Token Estimator

**File:** `meridian/router/token_estimator.py`

Pure functions, no state. Used for **routing decisions only** (not billing).

### How It Works

```python
_CHARS_PER_TOKEN = 4          # ~4 chars per token (OpenAI average)
_PER_MESSAGE_OVERHEAD = 4     # Role token + separators per message
_PER_REQUEST_OVERHEAD = 3     # Priming overhead for assistant reply
```

### estimate_prompt_tokens(messages)

```python
def estimate_prompt_tokens(messages: Any) -> int:
    total = _PER_REQUEST_OVERHEAD            # Start with 3
    for msg in messages:
        total += _PER_MESSAGE_OVERHEAD       # Add 4 per message
        total += _estimate_text_tokens(role) # Count role token
        for text in _content_to_text(content):
            total += _estimate_text_tokens(text)  # Count content
    return total
```

Example: `[{"role": "user", "content": "hi there"}]`
- Request overhead: 3
- Message overhead: 4
- Role "user": 1 token (4 chars / 4)
- Content "hi there": 2 tokens (8 chars / 4)
- **Total: 10 tokens**

### extract_max_tokens(body, default)

Pulls `max_tokens` or `max_completion_tokens` from request body. Returns `default` if missing or invalid.

### Multi-modal Content

```python
def _content_to_text(content: Any) -> Iterable[str]:
    """Yield textual parts of OpenAI message content."""
    if isinstance(content, str):
        yield content
        return
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    yield block.get("text", "")
```

Only counts text blocks. Images, audio → ignored (we route by text cost).

---

## 6. Routing Strategies

**File:** `meridian/router/strategies.py`

ABC pattern: every strategy implements `select(backends, request_ctx) -> Backend | None`.

### RequestContext (frozen dataclass)

```python
@dataclass(frozen=True)
class RequestContext:
    prompt_tokens: int   # Estimated from messages
    max_tokens: int      # From request body or default
    cost: float          # prompt * prefill_weight + max * decode_weight
```

Built once per request by `_build_request_context()` in the API layer.

### Strategy 1: WeightedRoundRobin

```python
class WeightedRoundRobin(RoutingStrategy):
    def select(self, backends, request_ctx=None):
        # Build pool: each backend appears `weight` times
        pool = []
        for b in backends:
            pool.extend([b] * b.weight)  # weight=80 means 80 copies
        random.shuffle(pool)
        # Cycle through the pool
        return next(self._cycle)
```

- Weight=80 vs weight=20 → 80% vs 20% traffic split
- Rebuilds cycle when eligible set changes
- Deterministic within a cycle

### Strategy 2: LeastInflight

```python
class LeastInflight(RoutingStrategy):
    def select(self, backends, request_ctx=None):
        return min(backends, key=lambda b: b.inflight)
```

Simple: pick the backend with the fewest active requests. Ignores `request_ctx`.

### Strategy 3: EWMALatency

```python
class EWMALatency(RoutingStrategy):
    def select(self, backends, request_ctx=None):
        return min(backends, key=lambda b: b.ewma_latency_ms)
```

Pick the backend with lowest average latency. Ignores `request_ctx`.

### Strategy 4: TokenAware (the inference-aware one)

```python
class TokenAware(RoutingStrategy):
    def select(self, backends, request_ctx=None):
        if request_ctx is None:
            # Fallback: least inflight with name tiebreak
            return min(backends, key=lambda b: (b.inflight, b.name))

        cost = request_ctx.cost

        def score(b: Backend) -> tuple[float, int, str]:
            latency_factor = b.ewma_latency_ms if b.ewma_latency_ms > 0 else 1.0
            base = (b.inflight_cost + cost) * latency_factor
            penalty = self._penalty(b)  # queue_depth * q_weight + gpu_mem * mem_weight
            return (base + penalty, b.inflight, b.name)

        return min(backends, key=score)
```

**Score formula:**
```
score = (backend.inflight_cost + request_cost) × latency_factor + penalty
```

Where:
- `inflight_cost` = sum of costs of all requests currently being processed
- `request_cost` = this request's estimated work
- `latency_factor` = backend's EWMA latency (or 1.0 if unproven)
- `penalty` = telemetry signals (queue depth, GPU memory)

**Tie-breaking:** score → inflight count → backend name (deterministic).

**Unproven backends:** EWMA=0 uses factor 1.0 (neutral), not 0. This prevents unproven backends from winning trivially.

### create_strategy Factory

```python
def create_strategy(name: str, **kwargs) -> RoutingStrategy:
    if name == "token_aware":
        return TokenAware(**kwargs)
    strategies = {
        "weighted_round_robin": WeightedRoundRobin,
        "least_inflight": LeastInflight,
        "ewma_latency": EWMALatency,
    }
    return strategies[name]()
```

---

## 7. Health Checker

**File:** `meridian/health/checker.py`

Background async task that pings backends periodically.

### How It Works

```python
class HealthChecker:
    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.interval_s)  # Every 5s
            tasks = [self._check_backend(b) for b in self.registry.all_backends()]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_backend(self, backend: Backend) -> None:
        url = f"{backend.url}{backend.health_endpoint}"
        try:
            resp = await self._client.get(url)
            if resp.status_code < 500:
                backend.record_health_success(self.config.success_threshold)
            else:
                backend.record_health_failure(self.config.fail_threshold)
        except (httpx.RequestError, Exception):
            backend.record_health_failure(self.config.fail_threshold)
```

### Two Failure Detection Mechanisms

1. **Active checking** (this module): Pings `/v1/models` every N seconds
2. **Passive checking** (request path): When a request to a backend fails (connection error or 5xx), `check_passive_failure()` is called from `main.py:230` and `main.py:264`

```python
def check_passive_failure(self, backend: Backend) -> None:
    """Called from the request path on connection/5xx errors."""
    backend.record_health_failure(self.config.fail_threshold)
```

This means backends get marked unhealthy from **actual traffic**, not just health pings.

### Lifecycle

- Created at `main.py:100`
- Started at `main.py:102` (in `init_app()`)
- Stopped at `main.py:130` (in `shutdown_app()`)

---

## 8. Proxy — Forwarding Requests

**File:** `meridian/proxy/forward.py`

Three functions that forward requests to backends using HTTPX.

### Shared HTTP Client

```python
_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None

def _get_or_create_client() -> httpx.AsyncClient:
    # Lazy creation, tied to current event loop
    # Connection pooling: 200 max connections, 50 keepalive
    # Timeouts: connect=5s, read=300s, write=5s, pool=5s
```

### forward_non_stream

```python
async def forward_non_stream(backend, body, request) -> JSONResponse:
    url = f"{backend.url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth  # Pass through (not validated)
    resp = await client.post(url, json=body, headers=headers)
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
```

### forward_stream

```python
async def forward_stream(backend, body, request) -> StreamingResponse:
    async def stream_generator() -> AsyncIterator[bytes]:
        req = client.build_request("POST", url, json=body, headers=headers)
        resp = await client.send(req, stream=True)
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk          # Zero-copy byte forwarding
        except asyncio.CancelledError:
            # Client disconnected → close upstream
            raise
        finally:
            await resp.aclose()

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
```

**Key detail:** No buffering. Bytes flow through as fast as the backend produces them.

### forward_get

```python
async def forward_get(backend, path) -> JSONResponse:
    url = f"{backend.url}{path}"
    resp = await client.get(url)
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
```

Used by `GET /v1/models`.

---

## 9. Telemetry System

**File:** `meridian/telemetry/`

Three files, clean separation of concerns.

### base.py — Interface + Data Model

```python
@dataclass(frozen=True)
class BackendTelemetry:
    """Per-backend capacity signals. All optional — None means 'unknown'."""
    queue_depth: Optional[int] = None
    tokens_per_sec: Optional[float] = None
    gpu_mem_util: Optional[float] = None  # 0.0–1.0

class TelemetryAdapter(ABC):
    """Pulls a BackendTelemetry snapshot from one backend."""
    @abstractmethod
    async def fetch(self) -> Optional[BackendTelemetry]:
        ...
```

### json_adapter.py — JSON-over-HTTP

```python
class JsonTelemetryAdapter(TelemetryAdapter):
    async def fetch(self) -> Optional[BackendTelemetry]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(self.url)
            if resp.status_code != 200:
                return None  # Lost signal
            payload = resp.json()
        except (httpx.RequestError, ValueError):
            return None  # Never raise
        return parse_payload(payload)
```

**Parsing is lenient:** wrong types, out-of-range values → silently become `None`. Never raises.

Expected JSON shape:
```json
{
  "queue_depth": 3,
  "tokens_per_sec": 250.0,
  "gpu_mem_util": 0.65
}
```

### poller.py — Background Poller

```python
class TelemetryPoller:
    async def _loop(self) -> None:
        await self._poll_all()  # Initial poll
        while True:
            await asyncio.sleep(self.interval_s)
            await self._poll_all()

    async def _poll_one(self, backend_name: str, adapter: TelemetryAdapter) -> None:
        backend = self.registry.get(backend_name)
        try:
            telemetry = await adapter.fetch()
        except Exception:
            telemetry = None
        if telemetry is None:
            backend.clear_telemetry()  # Signal lost → fall back to base scoring
        else:
            backend.set_telemetry(telemetry)
```

### Architectural Invariant

**Health gates eligibility. Telemetry tilts preference.**

Telemetry fetch failures MUST NEVER affect a backend's health. This is enforced in `poller.py:76-82` and tested in `test_telemetry_poller.py:62-72`.

```
Telemetry fails → clear signals → router uses base scoring
                                    (no capacity penalty)
Health check fails → mark unhealthy → backend removed from rotation
```

---

## 10. Metrics — Prometheus

**File:** `meridian/metrics/collectors.py`

Four Prometheus instruments:

```python
REQUESTS_TOTAL = Counter(
    "meridian_requests_total",
    "Total requests proxied",
    ["backend", "model", "status", "stream"],
)

REQUEST_LATENCY = Histogram(
    "meridian_request_latency_ms",
    "Request latency in milliseconds",
    ["backend", "model"],
    buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
)

BACKEND_INFLIGHT = Gauge(
    "meridian_backend_inflight",
    "Currently inflight requests per backend",
    ["backend"],
)

BACKEND_HEALTHY = Gauge(
    "meridian_backend_healthy",
    "Backend health status (1=healthy, 0=unhealthy)",
    ["backend"],
)
```

### When Metrics are Updated

In `main.py`, during the request lifecycle:

```
Request starts:
  BACKEND_INFLIGHT.inc()
  REQUESTS_TOTAL.inc()
  REQUEST_LATENCY.observe(latency)

Request ends:
  BACKEND_INFLIGHT.dec()
  BACKEND_HEALTHY.set(1 if healthy else 0)
```

### Exposed at

```
GET /metrics → Prometheus text format
```

---

## 11. Audit Logging — JSONL

**File:** `meridian/metrics/logger.py`

### What Gets Logged

```python
record = {
    "request_id": "mrdn-abc123def456",
    "timestamp": "2026-06-22T10:17:08.964Z",
    "model": "demo-model",
    "stream": true,
    "chosen_backend": "fast",
    "status_code": 200,
    "latency_ms": 42.57,
    "error_type": null
}
```

**What is NOT logged:** prompts, completions, request bodies, user identity, API keys, IP addresses.

### Implementation

```python
class RequestLogger:
    def __init__(self, jsonl_path: str) -> None:
        self._file = open(self._path, "a", buffering=1)  # Line-buffered

    def log(self, request_id, model, stream, backend, status_code, latency_ms, error_type=None):
        record = { ... }
        self._file.write(json.dumps(record) + "\n")
```

- Line-buffered (writes immediately)
- Append-only (never truncates)
- **Not tamper-evident** (plain file, no HMAC/Merkle chain)
- Lost on restart (no persistence guarantee)

---

## 12. API Layer — The Orchestrator

**File:** `meridian/api/main.py`

This is where everything connects. 348 lines that tie all modules together.

### Module-Level State

```python
_registry: Optional[BackendRegistry] = None
_strategy: Optional[RoutingStrategy] = None
_health_checker: Optional[HealthChecker] = None
_telemetry_poller: Optional[TelemetryPoller] = None
_request_logger: Optional[RequestLogger] = None
_config: Optional[MeridianConfig] = None
_recent_requests: deque[Dict[str, Any]] = deque(maxlen=100)
```

Set during `init_app()`, used by all endpoints.

### Startup Flow (init_app)

```python
async def init_app(config=None, start_health=True):
    # 1. Load config
    _config = config or _load_config()

    # 2. Create backends + registry
    backends = [Backend(bc) for bc in _config.backends]
    _registry = BackendRegistry(backends)

    # 3. Create routing strategy
    _strategy = create_strategy(
        _config.gateway.strategy,
        prefill_weight=_config.gateway.prefill_weight,
        decode_weight=_config.gateway.decode_weight,
        ...
    )

    # 4. Init Prometheus gauges
    for b in backends:
        BACKEND_HEALTHY.labels(backend=b.name).set(1)
        BACKEND_INFLIGHT.labels(backend=b.name).set(0)

    # 5. Start health checker (background pings)
    _health_checker = HealthChecker(_registry, _config.health)
    await _health_checker.start()

    # 6. Start telemetry poller (background scraping)
    adapters = {bc.name: JsonTelemetryAdapter(...) for bc in _config.backends if bc.telemetry}
    _telemetry_poller = TelemetryPoller(_registry, adapters, interval_s=poll_interval)
    await _telemetry_poller.start()

    # 7. Open JSONL log
    _request_logger = RequestLogger(_config.logging.jsonl_path)
```

### Request Flow (POST /v1/chat/completions)

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    # 1. Generate request ID + start timer
    request_id = generate_request_id()  # "mrdn-{uuid}"
    start = now_ms()

    # 2. Parse JSON body
    body = await request.json()
    model = body.get("model", "")
    is_stream = body.get("stream", False)

    # 3. Estimate tokens + compute cost
    request_ctx = _build_request_context(body)

    # 4. Select backend
    backend = _select_backend(model, request_ctx=request_ctx)
    if backend is None:
        return _error_json("No healthy backend", 503)

    # 5. Track inflight
    backend.increment_inflight()
    backend.add_inflight_cost(request_ctx.cost)
    BACKEND_INFLIGHT.labels(backend=backend.name).inc()

    # 6. Forward request
    try:
        if is_stream:
            resp = await forward_stream(backend, body, request)
            # Wrap in tracked_stream() for metrics on completion
            resp.body_iterator = tracked_stream()
            resp.headers["x-request-id"] = request_id
            resp.headers["x-meridian-backend"] = backend.name
            return resp
        else:
            resp = await forward_non_stream(backend, body, request)
            resp.headers["x-request-id"] = request_id
            resp.headers["x-meridian-backend"] = backend.name
            return resp
    except httpx.RequestError:
        # Connection error → passive health failure
        _health_checker.check_passive_failure(backend)
        return _error_json("Backend error", 502)
    finally:
        # 7. Decrement inflight, update latency, log
        backend.decrement_inflight()
        backend.subtract_inflight_cost(request_ctx.cost)
        backend.update_latency(latency)
        _request_logger.log(...)
        _record_request(...)  # Ring buffer for UI
```

### _build_request_context

```python
def _build_request_context(body):
    prompt_tokens = estimate_prompt_tokens(body.get("messages"))
    max_tokens = extract_max_tokens(body, _config.gateway.default_max_tokens)
    cost = prompt_tokens * _config.gateway.prefill_weight + max_tokens * _config.gateway.decode_weight
    return RequestContext(prompt_tokens=prompt_tokens, max_tokens=max_tokens, cost=cost)
```

### _select_backend

```python
def _select_backend(model, tags=None, request_ctx=None):
    eligible = _registry.eligible(model, tags)
    return _strategy.select(eligible, request_ctx)
```

### All Endpoints

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | Main inference endpoint (stream + non-stream) |
| `/v1/models` | GET | Lists models (proxies to first healthy backend) |
| `/meridian/status` | GET | Backend health, latency, inflight counts |
| `/meridian/requests` | GET | Last 100 requests (in-memory ring buffer) |
| `/metrics` | GET | Prometheus metrics |
| `/ui` | GET | Live dashboard HTML |

---

## 13. Mock Backend

**File:** `mock_backend/server.py`

Fake OpenAI-compatible server for testing. No real inference.

### What It Does

- `GET /v1/models` → returns configured model name
- `POST /v1/chat/completions` → echoes user message with configurable latency

### Non-streaming Response

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "demo-model",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "[fast] Echo: Hello!"},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 3,
    "total_tokens": 13
  }
}
```

### Streaming Response

```
data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"[fast] "}}]}
data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"Echo: "}}]}
data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"Hello!"}}]}
data: {"id":"chatcmpl-abc","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

### Configuration

Via environment variables:
- `BACKEND_NAME` — "fast" or "slow" (prefix in response)
- `BASE_LATENCY_MS` — simulated latency (50ms or 300ms)
- `MODEL_NAME` — model name to report

---

## 14. Live Dashboard — UI

**File:** `meridian/ui/index.html`

Single-page HTML/CSS/JS app. No framework, no build step.

### What It Shows

- **Strategy badge** — current routing strategy
- **Backend cards** — health status, inflight count, cost-inflight, EWMA latency, weight, engine, model, tags, URL, telemetry signals
- **Recent requests table** — request ID, backend, model, stream, status, latency, timestamp

### How It Works

```javascript
async function poll() {
    const [statusResp, reqResp] = await Promise.all([
        fetch('/meridian/status'),
        fetch('/meridian/requests'),
    ]);
    renderBackends(await statusResp.json());
    renderRequests(await reqResp.json());
}
poll();
setInterval(poll, 1000);  // Poll every second
```

Pure polling. No WebSocket. No authentication.

---

## 15. CLI Entry Point

**File:** `meridian/cli/main.py`

```python
def cli() -> None:
    parser = argparse.ArgumentParser(prog="meridian")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve")
    serve.add_argument("-c", "--config", default="config.yaml")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    if args.command == "serve":
        os.environ["MERIDIAN_CONFIG"] = args.config
        uvicorn.run("meridian.api.main:app", host=host, port=port)
```

Usage: `meridian serve -c config.yaml`

Exposed as `meridian` in `pyproject.toml`:
```toml
[project.scripts]
meridian = "meridian.cli.main:cli"
```

---

## 16. Tests

**Directory:** `tests/`

### Test Files

| File | Tests | What it covers |
|------|-------|----------------|
| `test_config.py` | 3 | Config loading, defaults, from_dict |
| `test_registry.py` | 5 | Eligibility filtering, inflight tracking, EWMA |
| `test_router.py` | 15 | All 4 strategies, scoring, tie-breaking, penalties |
| `test_token_estimator.py` | 10 | Token counting, edge cases, multimodal |
| `test_telemetry_json.py` | 8 | JSON parsing, type coercion, boundaries |
| `test_telemetry_poller.py` | 5 | Poller invariant: telemetry ≠ health |
| `test_api_integration.py` | 8 | E2E: mock backend → Meridian → assertions |

### Test Infrastructure

`test_api_integration.py` is the most complex:

1. Starts a real mock backend in a thread (random port)
2. Creates a Meridian app with `init_app()`
3. Tests via `httpx.ASGITransport` (no real network)

```python
@pytest.fixture
async def client():
    cfg = MeridianConfig.from_dict({...})
    await init_app(cfg, start_health=False)
    transport = httpx.ASGITransport(app=meridian_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

### Running Tests

```bash
ruff check .        # Linting
mypy meridian       # Type checking
pytest -q           # Tests
```

---

## 17. Docker Setup

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY meridian/ meridian/
RUN pip install --no-cache-dir .
COPY config.yaml .
CMD ["uvicorn", "meridian.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### docker-compose.yml

```yaml
services:
  backend-fast:
    build: ./mock_backend
    environment:
      - BACKEND_NAME=fast
      - BASE_LATENCY_MS=50
      - MODEL_NAME=demo-model
    ports: ["9101:9001"]

  backend-slow:
    build: ./mock_backend
    environment:
      - BACKEND_NAME=slow
      - BASE_LATENCY_MS=300
      - MODEL_NAME=demo-model
    ports: ["9102:9002"]

  meridian:
    build: .
    ports: ["9080:8080"]
    environment:
      - MERIDIAN_CONFIG=/app/config.yaml
    depends_on: [backend-fast, backend-slow]
    volumes:
      - ./config.yaml:/app/config.yaml:ro
```

Port mapping:
- Meridian: host `9080` → container `8080`
- Fast backend: host `9101` → container `9001`
- Slow backend: host `9102` → container `9002`

---

## 18. Request Flow — End to End

```
Client sends POST /v1/chat/completions
         │
         ▼
    ┌─────────────────────────────────────────────┐
    │  1. Parse JSON body                         │
    │  2. estimate_prompt_tokens(body.messages)   │
    │  3. extract_max_tokens(body, default)       │
    │  4. cost = prompt * prefill_w + max * dec_w │
    │  5. RequestContext(prompt, max, cost)        │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │  6. _registry.eligible(model, tags)         │
    │     → filter: healthy + model + tags        │
    │  7. _strategy.select(eligible, ctx)         │
    │     → pick one backend                      │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │  8. backend.increment_inflight()            │
    │  9. backend.add_inflight_cost(cost)         │
    │ 10. BACKEND_INFLIGHT.inc()                  │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │ 11. forward_stream() or forward_non_stream()│
    │     → HTTPX POST to backend                 │
    │     → stream bytes or return JSON           │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │ 12. backend.decrement_inflight()            │
    │ 13. backend.subtract_inflight_cost(cost)    │
    │ 14. backend.update_latency(latency)         │
    │ 15. REQUESTS_TOTAL.inc()                    │
    │ 16. REQUEST_LATENCY.observe(latency)        │
    │ 17. BACKEND_INFLIGHT.dec()                  │
    │ 18. _request_logger.log(...)                │
    │ 19. _record_request(...) → ring buffer      │
    └─────────────────┬───────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │ 20. Response with headers:                  │
    │     x-request-id: mrdn-{uuid}              │
    │     x-meridian-backend: {backend.name}      │
    └─────────────────────────────────────────────┘
```

---

## 19. Data Flow Diagram

```
config.yaml
    │
    ▼ (parsed by Pydantic)
MeridianConfig
    │
    ▼ (creates)
BackendRegistry ──────┐
RoutingStrategy ──────┤
HealthChecker ────────┤──> FastAPI app
TelemetryPoller ──────┤
RequestLogger ────────┘
    │
    ▼ (on each request)
Parse body → Token estimate → Cost calc → Backend select → Proxy → Track → Log
    │
    ▼ (background)
HealthChecker pings backends → toggles healthy flag
TelemetryPoller scrapes backends → sets queue_depth/gpu_mem_util
```

---

## 20. Running Instances

### Mock Backends (Docker)

```bash
cd ~/Desktop/OpenSource/Meridian
docker compose up --build
# Meridian: http://localhost:9080
# Fast mock: http://localhost:9101
# Slow mock: http://localhost:9102
```

### Real Backend (Ollama)

```bash
# 1. Start Ollama
ollama serve

# 2. Start Meridian with Ollama config
cd ~/Desktop/OpenSource/Meridian
MERIDIAN_CONFIG=config_real.yaml .venv/bin/uvicorn meridian.api.main:app --port 9081

# 3. Test
curl http://localhost:9081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Hello!"}]}'
```

---

## 21. Connecting to OpenAI SDK

Any OpenAI-compatible client works by changing `base_url`:

### Python

```python
from openai import OpenAI

client = OpenAI(
    api_key="anything",  # Meridian doesn't validate auth
    base_url="http://localhost:9081/v1"
)

r = client.chat.completions.create(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(r.choices[0].message.content)
```

### curl

```bash
curl http://localhost:9081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Hello"}]}'
```

### What Works

- `/v1/chat/completions` (stream + non-stream)
- `/v1/models`
- `Authorization` header pass-through
- Streaming SSE with `[DONE]` terminator

### What Doesn't Exist Yet

- `/v1/embeddings` — not proxied
- `/v1/batch` — not implemented
- API key validation
- Rate limiting
- Multi-tenancy

---

*End of walkthrough. Every module, every function, every connection explained.*
