# KV-cache-aware routing (experimental, unreleased)

`gateway.strategy: cache_affinity` adds a prefix-locality bonus to Meridian's
token-aware load score. The gateway stores bounded prefix hashes and predictions;
the inference engine owns the actual KV cache. This is distinct from session
affinity and does not require client session headers.

Implementation, mock tests and [local vLLM validation](./validation/KV_CACHE_VLLM.md)
are available on the capstone branch. Both vLLM 0.24.0 engines passed cold/hit
telemetry checks; 6,336 measured requests completed without failures. The sweeps
do **not** establish a consistent performance benefit from correction. Keep this
experimental and opt-in; it is not a released v0.12.0 capability.

## Configure

Start from `configs/cache_affinity.yaml`:

```yaml
gateway:
  strategy: cache_affinity
  cache_affinity:
    block_chars: 256
    max_checkpoints: 16
    ttl_s: 300
    max_entries: 100000
    locality_weight: 1000
    hit_threshold: 0.5
    min_trust: 0.15
    feedback_correction: true
```

The score is `TokenAware.score - locality_weight * prediction_trust` for the
predicted backend; other candidates keep the ordinary score. Telemetry penalties
still apply. The bonus is in the same score units as token-aware routing, so tune
it against the fleet's measured latency and queue costs. A sufficiently loaded
backend loses despite its cache prediction. Zero locality weight, absent
predictions, or absent engine telemetry preserve token-aware selection.

Health, model, tenant-pool, tiering, session-affinity and canary rules still take
precedence. A cache prediction cannot admit a backend those rules exclude.

## What is retained and learned

- Hashes cover cumulative serialized **text** chat prefixes, longest first.
  They approximate token prefixes; they are not engine-tokenizer hashes.
- Namespaces include model, authenticated organization, and prompt modifiers
  such as tools. Anonymous traffic shares a public namespace. Generation-only
  options such as temperature do not partition predictions.
- PII replacement runs before hashing. The index holds no raw prompt text.
  Hashes are never emitted as metric labels or audit fields.
- Text messages are supported; multimodal and unsupported message shapes use
  ordinary routing. Hashing is capped by block size × checkpoint count.
- Each process owns its own TTL index. Nearest-expiry eviction bounds entries;
  process restart and config reload discard predictions. In-flight responses
  update their original strategy rather than a replacement index.
- A successful cold response with valid cache telemetry seeds neutral trust
  (`0.5`). Later feedback raises or lowers trust with Laplace smoothing:
  `(hits + 1) / (hits + misses + 2)`. Below `min_trust`, the entry is evicted.
  `feedback_correction: false` keeps neutral trust for the static experiment arm.
- Missing, malformed, or out-of-range `cached_tokens` means **unknown**. It does
  not seed or decay the index. Failed or cancelled requests do not train it.

When this strategy handles supported streaming requests, Meridian requests
`stream_options.include_usage: true` from the upstream, even if the caller omitted
it. The standard usage chunk is passed through, followed by `[DONE]`. Feedback
works independently of cost attribution and budget enforcement.

## Metrics

| Metric | Meaning |
|---|---|
| `meridian_cache_affinity_decisions_total{outcome}` | Successful feedback evaluations: `predicted_hit`, `predicted_miss`, `no_prediction` |
| `meridian_cache_feedback_unavailable_total` | Successful supported requests without usable cache feedback |
| `meridian_cache_hit_ratio` | Histogram of engine cached/prompt tokens |
| `meridian_prefix_index_entries` | Index size at the last feedback update |
| `meridian_prefix_index_trust` | Histogram of evaluated prediction trust |

`predicted_miss` includes cases where policy or load sends traffic elsewhere.
Prediction accuracy is therefore the end-to-end success rate of predictions,
not a direct measurement of whether an unselected engine still holds a prefix.

## Local prerequisite: two dedicated vLLM instances

The current machine has an RTX 4060 Laptop GPU with 8 GiB VRAM. Docker Desktop
is stopped; the system Docker daemon runs but lacks an NVIDIA runtime. A native
vLLM environment avoids requiring system configuration changes. The following
container commands are a **recipe**, not an executed container result.
Two instances on one GPU emulate a fleet and contend for GPU resources; use
separate GPUs for production-representative results.

For a native installation, create a separate environment:

```bash
uv venv .venv-vllm --python 3.12
uv pip install --python .venv-vllm/bin/python 'vllm==0.24.0'
```

Run the following in two terminals, using port `18000` in the first and `18001`
in the second. Request-content logging is disabled by default in this version.
The explicit 512 × 16-token cache bounds create controlled cache pressure; they
are experiment settings, not production tuning recommendations.
`VLLM_SERVER_DEV_MODE=1` enables the cache-reset endpoint in v0.24.0; keep these
dedicated experiment servers bound to loopback.
`VLLM_USE_FLASHINFER_SAMPLER=0` selects the supported PyTorch sampler, avoiding
FlashInfer's native-extension compilation against this machine's older CUDA
toolkit and newer default GCC. Both engines use the same setting.

```bash
OMP_NUM_THREADS=2 VLLM_SERVER_DEV_MODE=1 VLLM_USE_FLASHINFER_SAMPLER=0 \
  .venv-vllm/bin/vllm serve Qwen/Qwen2.5-0.5B-Instruct \
  --host 127.0.0.1 --port 18000 \
  --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --gpu-memory-utilization 0.40 --max-model-len 2048 --max-num-seqs 8 \
  --block-size 16 --num-gpu-blocks-override 512 \
  --enforce-eager --enable-prefix-caching --enable-prompt-tokens-details
```

Alternatively, ensure Docker and the NVIDIA Container Toolkit work (`docker info` and
`nvidia-smi`). The KV validation recipe pins vLLM v0.24.0 and the
model revision below. vLLM v0.10.2 failed the [cold-cache probe](./validation/kv-cache-vllm-0.10.2-prerequisite.json):
it omits zero cached-token details. The [upstream fix](https://github.com/vllm-project/vllm/pull/44383)
is included in v0.24.0.
Verify cache telemetry with the probe before running
the sweep; a documented API field is not proof that a particular engine build
populates it.

```bash
docker run -d --name meridian-cache-vllm-a --gpus all --ipc=host \
  -e VLLM_SERVER_DEV_MODE=1 \
  -e VLLM_USE_FLASHINFER_SAMPLER=0 \
  -p 127.0.0.1:18000:8000 \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  vllm/vllm-openai:v0.24.0 \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --gpu-memory-utilization 0.40 --max-model-len 2048 --max-num-seqs 8 \
  --block-size 16 --num-gpu-blocks-override 512 \
  --enforce-eager --enable-prefix-caching --enable-prompt-tokens-details

docker run -d --name meridian-cache-vllm-b --gpus all --ipc=host \
  -e VLLM_SERVER_DEV_MODE=1 \
  -e VLLM_USE_FLASHINFER_SAMPLER=0 \
  -p 127.0.0.1:18001:8000 \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  vllm/vllm-openai:v0.24.0 \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --gpu-memory-utilization 0.40 --max-model-len 2048 --max-num-seqs 8 \
  --block-size 16 --num-gpu-blocks-override 512 \
  --enforce-eager --enable-prefix-caching --enable-prompt-tokens-details
```

Wait for `/v1/models` on both ports. If startup fails, inspect their logs and
reduce memory utilization or sequence count. The vLLM CLI documents
[`--enable-prompt-tokens-details`](https://docs.vllm.ai/en/v0.24.0/cli/serve.html);
the telemetry probe remains mandatory:

```bash
.venv/bin/python scripts/validate_cache_backend.py \
  --backend-url http://127.0.0.1:18000 --backend-url http://127.0.0.1:18001 \
  --reset-caches --model Qwen/Qwen2.5-0.5B-Instruct \
  --output docs/validation/kv-cache-prerequisite.json
```

The probe requires `--reset-caches` and dedicated experiment engines. It resets
the cache before each mode, requires explicit zero counts for cold sync and SSE
responses, then requires nonzero repeated-prefix hits in both modes. It saves
failed checks as well as successful ones to the JSON output. If it fails on this pinned engine,
record that failure, select a compatible version, and repeat the probe before
changing the benchmark image/version metadata. Do not substitute missing values
with measured zeroes.

## Three-arm sweep

The [executed local report](./validation/KV_CACHE_VLLM.md) contains pressure
calibration, two full sharing sweeps, a locality-weight sensitivity check, raw
rows, per-repeat spread, limitations and the exact commands. The larger recipe
below is available for subsequent experiments.

Arms are A = least-inflight, B = static prefix affinity, C = feedback-corrected
affinity. Every arm gets the same seeded workload and starts with a fresh gateway
index and reset engine caches. Repetitions rotate arm order. Warmup is excluded
from summaries. Unique warmup requests do not duplicate measured unique prompts.

Smoke without GPU:

```bash
.venv/bin/python scripts/bench_cache_routing.py \
  --requests 12 --warmup 2 --sharing-ratios 0 100 --repetitions 1 \
  --csv /tmp/kv-smoke.csv --output /tmp/kv-smoke.json
```

The checked-in [synthetic smoke report](./validation/kv-cache-mock-smoke.json)
and [per-request CSV](./validation/kv-cache-mock-smoke.csv) exercise all three arms
at 0% and 100% sharing. They prove harness operation, not a performance advantage;
the small run does not establish that correction improves routing.

Full synthetic sweep (LRU simulation, not engine performance):

```bash
.venv/bin/python scripts/bench_cache_routing.py \
  --requests 100 --warmup 8 --repetitions 3 --concurrency 4 \
  --csv /tmp/kv-mock.csv --output /tmp/kv-mock.json
```

Real sweep, **only against dedicated experiment engines**: the harness calls
vLLM's [`POST /reset_prefix_cache`](https://docs.vllm.ai/en/v0.10.0/api/vllm/entrypoints/openai/api_server.html)
before each arm. It refuses real mode without explicit `--reset-caches`.

```bash
.venv/bin/python scripts/bench_cache_routing.py \
  --backend-url http://127.0.0.1:18000 --backend-url http://127.0.0.1:18001 \
  --reset-caches --model Qwen/Qwen2.5-0.5B-Instruct \
  --engine-version vllm-0.24.0 \
  --model-revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --gpu-description 'RTX 4060 Laptop 8 GiB; two instances on one GPU' \
  --requests 200 --warmup 20 --repetitions 3 --concurrency 4 --request-rate 4 \
  --working-set 64 --prompt-chars 2048 \
  --csv docs/validation/kv-cache-vllm.csv --output docs/validation/kv-cache-vllm.json
```

JSON records per-repeat cache-hit ratio, prediction accuracy, client-measured
TTFT p50/p95, parameters, code revision, dirty-tree status and supplied engine/GPU
metadata. CSV retains per-request metadata only. Runs fail on request errors,
missing feedback, missing first content token, or incomplete SSE termination.

The rate schedules request starts, with concurrency as a cap; once saturated,
starts queue locally. TTFT begins when a request is dispatched, so use a rate
below sustained service capacity when comparing engine latency.

First calibrate cache pressure: sweep working-set size and prompt length until
eviction occurs. If all arms approach 100% cache hits, the experiment cannot
measure correction. Publish every repeat and its spread, including null results.
Do not infer a benefit from a small synthetic smoke run.

To run the gateway interactively after the probe:

```bash
MERIDIAN_CONFIG=configs/cache_affinity.yaml .venv/bin/uvicorn meridian.api.main:app --port 8080
```

For native servers, stop each experiment terminal with Ctrl-C. For containers,
stop only the two experiment containers when finished:

```bash
docker stop meridian-cache-vllm-a meridian-cache-vllm-b
```
