# Local KV routing validation

Recorded 2026-10-12 local time (2026-10-11 UTC), on development revision
`bbdd62cab26437a1e5fd57902e542454f8119d98`. This is unreleased branch evidence,
not validation of the published v0.12.0 image.

Both dedicated vLLM 0.24.0 engines passed cold and repeated-prefix checks in JSON
and SSE. Across the two full sweeps and one weight sensitivity check, **6,336
measured requests completed with zero failures**, excluding 792 warmup requests.
Every measured B/C request produced an accounted feedback decision.

**The performance hypothesis is not established.** Correction sometimes helps,
but it also worsens tail latency in some cases. The feature remains experimental
and opt-in; the default locality weight remains 1000. These results do not justify
claiming that correction improves performance generally or enabling it by default.

## Environment and prerequisites

- RTX 4060 Laptop, 8 GiB VRAM, driver 580.178.04; two independent engines share it.
- Qwen/Qwen2.5-0.5B-Instruct, revision `7ae557604adf67be50417f59c2c2f167def9a775`.
- Native vLLM 0.24.0, PyTorch 2.11.0/CUDA 13.0; eager execution and PyTorch sampler.
- Each engine: 0.40 GPU utilization setting, 2048 maximum model length, 8 sequences,
  512 cache blocks of 16 tokens (8192 KV tokens). No engine source patches.
- Full commands, selected environment variables and installed dependencies:
  [environment manifest](./kv-cache-vllm-environment.json).
- [Passing prerequisite report](./kv-cache-prerequisite.json): both engines returned
  `cached_tokens=0` cold and `cached_tokens=592` of 599 prompt tokens on repeats,
  in both regular and streaming modes.
- [vLLM 0.10.2 failure](./kv-cache-vllm-0.10.2-prerequisite.json): cold sync usage
  omitted cache details, despite repeated requests reporting positive hits.
  The upstream zero-count omission is fixed in the version used for the sweeps.
- [Gateway smoke](./kv-cache-vllm-gateway-smoke.json) covers live models, regular
  chat, streaming termination, Meridian headers, and direct/gateway overhead.

The native recipe in [KV_CACHE_ROUTING.md](../KV_CACHE_ROUTING.md) documents the
optional FlashInfer compilation workaround and loopback-only development reset
endpoint. System Docker configuration was unchanged. Both experiment engines
were stopped after validation.

## Cache pressure

The [calibration report](./kv-cache-vllm-calibration.json) records both engines.
An anchor prompt had 1137 tokens. After warming it, 1136 were cached (99.9%).
It retained those tokens after 4 distinct prompts, but only 16 tokens (1.4%)
remained after 16 or 64 distinct prompts. This directly demonstrates eviction.
The comparison therefore uses a 16-prefix working set under that cache limit.
Sequential calibration throughput was about 18 requests/second per engine.

Calibration is reproducible using `workload` from `scripts/bench_cache_routing.py`:
reset each engine, send `workload(1, 0, seed=42, working_set=1, prompt_chars=2048)[0]`
twice, send N requests from the same function with `offset=1`, then resend the
anchor. Use N = 4, 16, 64; set model, `stream=False`, omit `stream_options`, and
retain `max_tokens=8` and `temperature=0`.

## Results

A = least-inflight; B = static affinity; C = feedback-corrected affinity.
All cells summarize three repetitions of 64 measured requests. Hit ratio, p50,
p95 and accuracy are means of the per-repeat summaries; brackets show the
minimum and maximum **per-repeat p95**, not confidence intervals. The linked JSON
reports retain every repetition, including prediction counts and all percentiles.
The CSV files retain all measured request metadata; prompts are not retained.

### Fixed offered load: 8 requests/second, concurrency cap 4

[Per-repeat JSON](./kv-cache-vllm.json) · [Raw CSV](./kv-cache-vllm.csv)

| Sharing | Arm | Hit ratio | TTFT p50 ms | TTFT p95 ms [range] | Prediction accuracy |
|---|---|---|---|---|---|
| 0% | A | 0.014 | 51.48 | 53.86 [53.53–54.07] | — |
| 0% | B | 0.014 | 51.24 | 53.08 [51.11–54.13] | — |
| 0% | C | 0.014 | 51.73 | 54.04 [53.78–54.43] | — |
| 25% | A | 0.044 | 51.03 | 53.41 [52.90–54.15] | — |
| 25% | B | 0.054 | 51.12 | 53.68 [53.19–54.13] | 0.308 |
| 25% | C | 0.046 | 50.73 | 53.49 [52.71–54.01] | 0.225 |
| 50% | A | 0.101 | 51.30 | 53.37 [53.29–53.53] | — |
| 50% | B | 0.096 | 51.54 | 53.84 [53.57–54.17] | 0.242 |
| 50% | C | 0.091 | 51.18 | 53.88 [52.80–54.60] | 0.228 |
| 75% | A | 0.241 | 50.42 | 52.61 [50.55–54.18] | — |
| 75% | B | 0.231 | 50.55 | 53.00 [51.44–54.53] | 0.359 |
| 75% | C | 0.257 | 50.54 | 52.57 [50.60–53.82] | 0.400 |
| 100% | A | 0.502 | 39.00 | 54.49 [53.88–55.54] | — |
| 100% | B | 0.486 | 34.75 | 54.13 [53.67–54.51] | 0.552 |
| 100% | C | 0.486 | 35.40 | 53.58 [51.93–54.44] | 0.552 |

### Closed-loop contention: concurrency 8, no rate limit

[Per-repeat JSON](./kv-cache-vllm-burst.json) · [Raw CSV](./kv-cache-vllm-burst.csv)

| Sharing | Arm | Hit ratio | TTFT p50 ms | TTFT p95 ms [range] | Prediction accuracy |
|---|---|---|---|---|---|
| 0% | A | 0.014 | 265.60 | 353.65 [349.69–359.48] | — |
| 0% | B | 0.014 | 267.21 | 354.36 [345.96–361.29] | — |
| 0% | C | 0.014 | 268.67 | 358.88 [355.04–362.12] | — |
| 25% | A | 0.025 | 265.55 | 351.42 [343.04–355.78] | — |
| 25% | B | 0.040 | 266.81 | 363.69 [348.00–384.05] | 0.200 |
| 25% | C | 0.040 | 265.45 | 355.23 [348.88–360.91] | 0.153 |
| 50% | A | 0.078 | 264.05 | 349.61 [345.00–353.86] | — |
| 50% | B | 0.079 | 259.60 | 375.35 [358.43–408.21] | 0.101 |
| 50% | C | 0.099 | 261.46 | 355.21 [345.05–360.69] | 0.172 |
| 75% | A | 0.255 | 205.23 | 339.85 [321.67–351.52] | — |
| 75% | B | 0.253 | 212.94 | 349.38 [347.95–351.53] | 0.324 |
| 75% | C | 0.252 | 205.41 | 381.52 [327.99–418.05] | 0.321 |
| 100% | A | 0.453 | 179.20 | 276.19 [267.74–281.35] | — |
| 100% | B | 0.444 | 172.85 | 319.59 [273.26–343.82] | 0.392 |
| 100% | C | 0.444 | 178.07 | 317.52 [276.62–341.64] | 0.412 |

### Weight sensitivity: 100% sharing, concurrency 8, locality weight 10000

[Per-repeat JSON](./kv-cache-vllm-weight-10000.json) · [Raw CSV](./kv-cache-vllm-weight-10000.csv)

| Sharing | Arm | Hit ratio | TTFT p50 ms | TTFT p95 ms [range] | Prediction accuracy |
|---|---|---|---|---|---|
| 100% | A | 0.449 | 182.13 | 276.64 [265.41–283.40] | — |
| 100% | B | 0.430 | 169.14 | 304.04 [275.02–333.20] | 0.427 |
| 100% | C | 0.409 | 186.82 | 298.52 [274.44–324.42] | 0.324 |

At 8 requests/second and 75% sharing, C's mean hit ratio was 25.7% versus B's
23.1%, while mean p95 differed by less than 0.5 ms. Under contention at the same
sharing ratio, C's mean p95 was **381.52 ms versus B's 349.38 ms** (9.2% worse),
with similar hit ratios. At 100% sharing under contention, both affinity arms
had worse mean p95 than A. The higher-weight check also mixed gains and losses
across repeats. There is no consistent correction benefit in these measurements.

## Interpretation limits

- One shared laptop GPU, a small model, artificial cache limits, eager execution,
  a synthetic text workload, one comparison working set and three repetitions
  do not establish production fleet performance or statistical significance.
- At 8 requests/second, least-inflight's stable tie behavior sends all measured
  A requests to engine-0. It already provides incidental locality. B and C use
  token-aware load scores, so A versus B changes load scoring as well as locality;
  B versus C is the comparison that isolates correction.
- At 0% application-prefix sharing, the common model chat template still yields
  about 1.4% cached tokens. This is not accidental duplication of unique prompts.
- Prediction accuracy includes load/policy diversions from the predicted engine;
  it is not an independent residency probe for unselected engines.
- TTFT starts at dispatch and excludes waiting for the harness's concurrency
  semaphore. The contention run was closed-loop; a GPU sample reached 100%
  utilization. It is not a production capacity estimate.
- Order rotates by repeat, but shared-GPU contention and thermal/clock variation
  remain. Differences between the two load modes are not isolated causal effects.

## Reproduce the sweeps

Start two dedicated engines with the documented settings and pass the prerequisite
probe first. The following Bash function uses the exact measured workload settings:

```bash
meridian_cache_bench() {
  .venv/bin/python scripts/bench_cache_routing.py \
    --backend-url http://127.0.0.1:18000 --backend-url http://127.0.0.1:18001 \
    --reset-caches --model Qwen/Qwen2.5-0.5B-Instruct \
    --engine-version vllm-0.24.0 \
    --model-revision 7ae557604adf67be50417f59c2c2f167def9a775 \
    --gpu-description 'RTX 4060 Laptop 8 GiB; two instances on one GPU; eager; 512x16 KV tokens each; PyTorch sampler' \
    --requests 64 --warmup 8 --repetitions 3 --working-set 16 --prompt-chars 2048 "$@"
}
meridian_cache_bench --concurrency 4 --request-rate 8 \
  --csv /tmp/kv-fixed.csv --output /tmp/kv-fixed.json
meridian_cache_bench --concurrency 8 \
  --csv /tmp/kv-burst.csv --output /tmp/kv-burst.json
meridian_cache_bench --concurrency 8 --sharing-ratios 100 --locality-weight 10000 \
  --csv /tmp/kv-weight.csv --output /tmp/kv-weight.json
```

Every arm starts with a fresh Meridian process and reset engine caches. Seed 42
increments by repeat; arm order rotates. Unique warmup IDs differ from measured
unique IDs. Fixed-load and contention runs use locality weight 1000. No defaults
were tuned against a selected winning run.
