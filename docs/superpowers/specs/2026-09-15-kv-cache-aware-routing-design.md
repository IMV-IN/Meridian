# KV-Cache-Aware Routing — Design

**Date:** 2026-09-15
**Status:** Approved, pending implementation plan
**Author:** Capstone project
**Repo:** Meridian (`IMV-IN/Meridian`)

---

## 1. Context

Meridian routes requests across OpenAI-compatible inference backends using strategies that
are **cache-blind**: `weighted_round_robin`, `least_inflight`, `ewma_latency`, and
`token_aware` (`meridian/router/strategies.py`). None of them know anything about what the
engine has *already computed*.

This matters because modern engines — vLLM, SGLang — run automatic prefix caching (APC).
When a request's prompt shares a prefix with a recently-served request on the *same*
backend, the engine skips prefill for the shared span and time-to-first-token drops
sharply. When the same request lands on a *different* backend, that work is thrown away and
recomputed.

So there is a real, measurable, entirely gateway-side optimisation available: route
requests that share prefixes to the backend that already holds that prefix in KV cache.

**Why this is hard, and why it is not just session affinity.** Meridian already ships
`SessionStore` (`meridian/router/affinity.py`) which pins a *session id* to a backend. A
prefix index looks superficially similar and is not the same problem:

1. **The cache is not durable.** vLLM's APC evicts under GPU memory pressure. A sticky
   mapping goes stale, and a stale mapping is worse than none — you pay the routing
   decision and buy zero cache hit.
2. **Matching is fuzzy.** APC is a radix tree with longest-prefix match. Request B sharing a
   prefix with A should hit A's cached span up to the divergence point. An exact-prompt hash
   cannot express this.
3. **There is ground truth available.** vLLM reports `prompt_tokens_details.cached_tokens`
   on every response. Most gateway-side caching schemes have no way to know whether their
   prediction was right. Here we can *close the loop*.

That last point is the contribution. This design predicts, measures whether the prediction
paid off, and corrects.

### Prior art in this repo (reuse, do not rebuild)

| Component | Path | Role here |
|---|---|---|
| `SessionStore` | `meridian/router/affinity.py` | Direct template for `PrefixIndex` — TTL map, slide-on-hit, nearest-expiry eviction, injectable clock |
| `TokenAware` | `meridian/router/strategies.py:92` | Template for `CacheAffinity`; its load term is reused, not reinvented |
| `BackendTelemetry` | `meridian/telemetry/base.py` | Shape precedent: all-optional signals, `None` = unknown, never penalise on absence |
| `usage_from_dict` / `usage_from_sse_bytes` | `meridian/cost/extract.py:16,33` | **The tap point.** Already parses `usage`, currently discards `cached_tokens` |
| Usage scrape on stream | `meridian/api/main.py:234-265` | Existing SSE tail-scrape; the feedback signal rides the same path |
| `bench_overhead.py` | `scripts/bench_overhead.py` | Harness shape to follow: CLI args, `--json` summary, results table |

---

## 2. Goals

- Route requests with shared prefixes to the backend that holds that prefix, measurably
  improving cache-hit ratio and TTFT versus cache-blind routing.
- **Close the loop** on predictions using engine-reported `cached_tokens`, so the index
  self-corrects when the engine evicts.
- Produce a defensible empirical result: three-arm comparison across a controlled sweep.
- Stay gateway-side. No engine patches, no vLLM forks, no CUDA.

## 3. Non-goals

- **Not engine-agnostic.** `cached_tokens` is a vLLM/OpenAI-compatible field. Ollama does
  not report it. This is a vLLM-targeted feature and the docs will say so.
- **Not true prefill/decode disaggregation.** Separate problem; listed as deferred in
  `docs/FULL.md`.
- **Not a cache implementation.** The gateway never stores KV state. It only predicts and
  observes.
- **Not changing engine scheduling.** No control of continuous batching or KV allocators.

---

## 4. Architecture

```
                      ┌───────────── request ─────────────┐
                      ▼                                   │
              build prefix keys                           │
         (cumulative hashes at block boundaries)          │
                      │                                   │
                      ▼                                   │
   ┌──────► PrefixIndex.lookup(keys) ──► predicted backend│
   │                  │                                   │
   │                  ▼                                   │
   │        CacheAffinity.select(backends, ctx, index) ────┘
   │                  │
   │                  ▼
   │              proxy → vLLM backend
   │                  │
   │                  ▼
   │        response carries cached_tokens
   │                  │
   │                  ▼
   └──── PrefixIndex.record(keys, backend, hit_ratio) ◄── cost/extract.py
```

### 4.1 Components

| Unit | File | Responsibility | Depends on |
|---|---|---|---|
| `prefix_keys()` | `meridian/router/cache_key.py` (new) | Serialize a chat body → ordered list of cumulative prefix hashes, longest first | stdlib `hashlib`, `json` |
| `PrefixIndex` | `meridian/router/prefix_index.py` (new) | Bounded TTL map: prefix hash → `{backend, hits, misses, last_hit_ms}`. Pure, clock-injected, thread-safe | nothing |
| `CacheAffinity` | `meridian/router/strategies.py` | Score backends by load − locality + staleness | `PrefixIndex`, `Backend` |
| usage extension | `meridian/cost/extract.py` | Also return `cached_tokens` | — |
| feedback wiring | `meridian/api/main.py`, `finalize.py` | Thread `cached_tokens` → `PrefixIndex.record()` | — |
| harness | `scripts/bench_cache_routing.py` (new) | Generate sweep, drive load, collect per-request metrics | `httpx` |

Each unit is independently understandable and testable. `PrefixIndex` and `prefix_keys()` in
particular have no I/O and no clock except an injected one.

### 4.2 The routing key — hierarchical prefix hashing

vLLM's APC matches the **longest** cached prefix. To mirror that, a request is represented
not by one key but by a **chain**:

```
body → serialized prompt text → cumulative hashes at block boundaries

blocks:  [0:256)  [0:512)  [0:768)  [0:1024) ...
keys:     h1        h2        h3        h4        (each a blake2b of the cumulative span)
```

Lookup probes **longest → shortest**, first hit wins. This gives longest-prefix-match
semantics: a request sharing 1024 chars with a cached request hits `h4`; one sharing only
256 chars still hits `h1`.

**Why block boundaries on serialized text, not token boundaries:** Meridian's token
estimator (`meridian/router/token_estimator.py`) is a documented heuristic
(`~4 chars per token`), not a real tokenizer. Hashing text is honest about that; pretending
to hash token ids would be false precision. Block size is configurable.

**Why cumulative rather than per-block:** a per-block hash set would match on *any* shared
span, including one in the middle of a prompt. APC caches the leading span only, so
matching mid-prompt spans would generate predictions that cannot pay off.

Config: `block_chars` (default 256), `max_checkpoints` (default 16, bounding per-request
hashing cost to ~4 KB of text).

### 4.3 `PrefixIndex`

Modeled on `SessionStore` (`meridian/router/affinity.py`). Same proven shape: single lock,
TTL in ms, injectable clock, nearest-expiry eviction when full.

```python
@dataclass
class Entry:
    backend: str
    hits: int = 0
    misses: int = 0
    last_hit_ms: float = 0.0
    last_update_ms: float = 0.0
```

API: `lookup(keys) -> Optional[str]`, `record(keys, backend, hit_ratio)`, `sweep()`,
`size()`.

`lookup` slides TTL on hit (as `SessionStore` does). `record` is where correction happens:

- `hit_ratio >= hit_threshold` (default 0.5) → `hits += 1`, entry is trustworthy
- otherwise → `misses += 1`

**Staleness.** Trust score is `(hits + 1) / (hits + misses + 2)` — a Laplace-smoothed
estimate (a Beta(1,1) prior). Key behaviours:

- A brand-new entry scores **0.5** (neutral) so the first prediction is attempted; you
  cannot learn without trying.
- After 1 miss it drops to 0.33, after 3 misses to 0.20, after 6 to 0.125.
- An entry is **evicted when trust falls below `min_trust`** (default 0.15), checked inside
  `record()`. This is the eviction-under-pressure adaptation.
- A hit raises trust, so a genuinely resident prefix survives.

This is the mechanism that makes the design robust to APC eviction. Blind stickiness has no
equivalent and will keep routing to a backend that has long since evicted the span.

### 4.4 `CacheAffinity` strategy

Sibling to `TokenAware` (`strategies.py:92`). Reuses its base term verbatim:

```
latency_factor = b.ewma_latency_ms if b.ewma_latency_ms > 0 else 1.0
base           = (b.inflight_cost + request_ctx.cost) * latency_factor
locality       = locality_weight * trust(entry) if entry.backend == b.name else 0.0
score          = base - locality
```

Note there is exactly **one** locality term. Low trust shrinks the bonus to zero on its own,
so no separate staleness penalty is needed — adding one would double-count the same signal.
A fully-decayed entry is evicted by `PrefixIndex.record()` before it can be scored.

Determinism preserved: ties break on `(inflight, name)`, matching `TokenAware`.

**Safety:** routing must never be *worse* than the baseline when there is no useful
prediction. With no entry, or with `locality_weight = 0`, the score reduces exactly to
`TokenAware`'s. **Locality is a bonus, never a filter — a predicted-but-overloaded backend
must still lose to an idle one.** This is the key correctness property and gets a dedicated
test.

Fallback when `request_ctx is None` (health probe path): `least_inflight`, matching
`TokenAware`.

### 4.5 The feedback loop — the tap

`meridian/cost/extract.py` currently returns `Optional[Tuple[int, int]]` and reads
`usage.prompt_tokens` / `usage.completion_tokens` (lines 16–30). vLLM additionally sends:

```json
"usage": {
  "prompt_tokens": 1024,
  "completion_tokens": 12,
  "prompt_tokens_details": { "cached_tokens": 896 }
}
```

**Change:** return a small dataclass `Usage(prompt_tokens, completion_tokens, cached_tokens)`
instead of a bare tuple. Callers are at `meridian/api/main.py:263` (stream) and `:305`
(non-stream), plus `meridian/api/pipeline.py:194` (`reconcile_budget_usage`) and tests.

**Known defect to fix in the same change:** the SSE fallback regex
`_USAGE_RE = rb'"usage"\s*:\s*(\{[^}]+\})'` (`extract.py:13`) uses `[^}]+`, which stops at
the first `}` and therefore cannot capture a nested `prompt_tokens_details`. The structured
`json.loads` path is unaffected; the regex is a last-resort fallback. Widen it or document
that `cached_tokens` is unavailable on that path.

**Streaming caveat:** the SSE usage chunk only arrives if the client sends
`stream_options: {"include_usage": true}`. The gateway must set this itself when it intends
to measure, rather than relying on the client. Note the existing guard `_wants_usage_scrape`
(`main.py:51`) already gates whether the tail is scanned at all.

### 4.6 Observability

Per `CLAUDE.md` §5, Prometheus labels must avoid unbounded cardinality. **A prefix hash must
never be a label.** Counts and histograms only:

- `meridian_cache_affinity_decisions_total{outcome}` — `predicted_hit` / `predicted_miss` /
  `no_prediction`
- `meridian_cache_hit_ratio` — histogram of `cached_tokens / prompt_tokens`
- `meridian_cache_hit_ratio{strategy}` is acceptable — strategy names are bounded
- `meridian_prefix_index_entries` — gauge
- `meridian_prefix_index_trust` — histogram of trust scores (bounded buckets)

---

## 5. Experiment design

Three arms, identical workload, model, and hardware:

| Arm | Strategy | Isolates |
|---|---|---|
| **A** | `least_inflight` | cache-blind baseline |
| **B** | `cache_affinity`, static TTL, correction disabled | blind stickiness |
| **C** | `cache_affinity` + feedback correction | the contribution |

**Claims:** B > A proves locality routing works at all. C > B proves the feedback loop earns
its place. Two clean claims rather than one muddy one.

**Independent variable:** prefix-sharing ratio, swept at 0 / 25 / 50 / 75 / 100%.

**Dependent variables:**
- `cached_tokens / prompt_tokens` — engine-reported ground truth, per request
- TTFT p50/p95 — measured **client-side** in the harness (end-to-end, not self-reported by
  the gateway)
- index prediction accuracy — fraction of `predicted_hit` decisions that landed

**Controls:** fixed model, fixed prompt length distribution, fixed request rate, fixed
`gpu_memory_utilization` per instance, warmup excluded, N repetitions with reported spread.

**Harness:** `scripts/bench_cache_routing.py`, following `scripts/bench_overhead.py`'s shape
— CLI args, `--json` machine-readable summary, table output. Emits per-request rows
(request id, arm, sharing ratio, prompt_tokens, cached_tokens, ttft_ms, backend) to CSV so
figures are reproducible from raw data.

### 5.1 The ceiling effect — the primary risk

**If the aggregate KV cache is large enough that prefixes never evict, every arm converges
to ~100% hit ratio and all three tie.** The result looks like a bug and is actually a
mis-sized experiment.

The KV cache must therefore be sized so eviction genuinely happens in the high-sharing
regime. Levers: `gpu_memory_utilization` per instance, prompt length, and sharing ratio.
The sweep is what makes this tractable — it locates the regime where strategies separate.
**Calibrating this is the first experiment, not an afterthought.**

---

## 6. Testing

Per `CLAUDE.md` §5, nothing lands without tests.

**Unit (no GPU, no network):**
- `prefix_keys()`: determinism; identical prefixes → identical hashes; divergence point
  correct; `max_checkpoints` cap honoured; empty/malformed body → empty list.
- `PrefixIndex`: TTL expiry; slide-on-hit; nearest-expiry eviction at capacity; trust rises
  with hits and decays with misses; eviction at `min_trust`; `sweep()` correctness;
  thread-safety under concurrent `record`.
- `CacheAffinity`: **degenerates exactly to `TokenAware` with no prediction and with
  `locality_weight=0`** (the critical safety property); an overloaded predicted backend
  loses to an idle one; ties are deterministic on `(inflight, name)`; `request_ctx is None`
  falls back to `least_inflight`.
- `extract.py`: parses `prompt_tokens_details.cached_tokens`; absent field → `0` not `None`-
  crash; the SSE regex path.

**Integration (mock backend):**
- Feedback loop end-to-end: a response with `cached_tokens` updates the index and changes the
  next routing decision for the same prefix.
- Sweep harness smoke test against the mock backend.

**Manual (real vLLM, RTX 4060):** the three-arm sweep, results recorded in
`docs/validation/`.

**Quality gates (unchanged, `CLAUDE.md` §6):**
```bash
ruff check . && mypy meridian && pytest -q
```

---

## 7. Risks and limitations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Ceiling effect** — no eviction, all arms tie (§5.1) | Calibrate cache size first; the sweep locates the separating regime |
| 2 | **`cached_tokens` absent in streaming** without `include_usage` | Gateway sets `stream_options` itself when measuring |
| 3 | **Two vLLM instances on one 4060 is a fleet emulation**, not a real fleet — they contend for VRAM | State plainly as a limitation; real validation wants separate GPUs |
| 4 | **vLLM-specific** — `cached_tokens` is not in the Ollama path | Scope choice; documented, not implied away |
| 5 | Hash collisions in the prefix index | blake2b truncated to 128 bits; collision probability negligible and a collision degrades to a wrong prediction, which the feedback loop corrects |
| 6 | Index memory growth | Bounded size + TTL + nearest-expiry eviction, as `SessionStore` |
| 7 | Routing regression when prediction is wrong | Locality is a *bonus*, never a filter (§4.4); degenerate-to-`TokenAware` test |

**Deliberate non-goals restated as limitations:** not engine-agnostic, not disaggregation,
not a cache.

---

## 8. First task — de-risk before building

**Before any implementation:** stand up two vLLM instances on the 4060 and confirm
empirically that:

1. `prompt_tokens_details.cached_tokens` appears in a non-streaming response body;
2. it appears in the streaming SSE usage chunk when `stream_options.include_usage` is set;
3. APC is active and reports a non-zero hit for a repeated prefix.

The entire design rests on this one empirical fact. It is a ~30-minute check that de-risks
weeks of work, and it is the first item in the implementation plan.
