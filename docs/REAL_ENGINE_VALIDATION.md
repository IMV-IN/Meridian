# Real-engine release validation

Meridian release candidates are validated against live Ollama and vLLM servers,
not only the in-process mock backend. The validation harness checks the backend
first, starts a disposable Meridian process, runs the existing gateway smoke
test, and records a direct-versus-gateway benchmark as JSON.

## Validation matrix

| Engine | Runtime | Model | Status for v0.12.0 | Evidence |
|--------|---------|-------|--------------------|----------|
| Ollama | 0.31.1 | `qwen2.5:0.5b` | Passed at concurrency 1, 4, and 8 | [`serial`](./validation/ollama-v0.12.0.json), [`c4`](./validation/ollama-v0.12.0-c4.json), [`c8`](./validation/ollama-v0.12.0-c8.json) |
| vLLM | `vllm/vllm-openai:v0.10.2` | `Qwen/Qwen2.5-0.5B-Instruct` | Pending local GPU run | Evidence added after a passing run |

The vLLM profile pins model revision
`7ae557604adf67be50417f59c2c2f167def9a775` so a later model update cannot
silently change release evidence.

## Checks performed

Each profile verifies:

1. Direct `GET /v1/models` returns at least one model.
2. Direct non-stream chat returns at least one choice.
3. Direct streaming chat ends with `data: [DONE]`.
4. Meridian starts from a generated, isolated configuration.
5. `scripts/smoke_test.py` passes models, sync, stream, and response-header checks.
6. `scripts/bench_overhead.py` completes with no direct or gateway errors.
7. Evidence records Meridian, engine, Python, platform, GPU, and benchmark details.

Prompts and generated text are not written to the evidence file.

## Ollama

Prerequisites are Ollama, `curl`, and a Meridian development installation. The
profile uses an existing Ollama server when available. Otherwise, it starts one
for the validation and stops it afterward.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
PYTHON=.venv/bin/python sh scripts/validate_ollama.sh
```

Override the defaults with environment variables:

```bash
MODEL=llama3.2:3b REQUESTS=20 CONCURRENCY=1 \
  OUTPUT=/tmp/ollama-validation.json \
  PYTHON=.venv/bin/python sh scripts/validate_ollama.sh
```

## vLLM

Prerequisites are Docker, the NVIDIA Container Toolkit, an NVIDIA GPU, `curl`,
and enough disk space for the pinned image and model. The profile removes its
container on exit and reuses the host Hugging Face cache.

```bash
PYTHON=.venv/bin/python sh scripts/validate_vllm.sh
```

Useful overrides for constrained GPUs:

```bash
GPU_MEMORY_UTILIZATION=0.65 MAX_MODEL_LEN=2048 \
  PYTHON=.venv/bin/python sh scripts/validate_vllm.sh
```

## Generic OpenAI-compatible backend

For an already-running server, call the common harness directly:

```bash
.venv/bin/python scripts/validate_real_backend.py \
  --engine custom \
  --engine-version 1.0.0 \
  --backend-url http://127.0.0.1:8000 \
  --model my-model \
  --output /tmp/custom-validation.json
```

Use a free `--gateway-port` if port `18080` is occupied.

## Release procedure

1. Check out the exact release tag on the GPU validation host.
2. Run both profiles without changing their pinned defaults.
3. Confirm every check is `passed` and both benchmark error counts are zero.
4. Review the environment metadata and benchmark values for obvious anomalies.
5. Commit the evidence files with the release documentation.

Latency is informational because it varies by host, engine state, thermals, and
driver version. Functional failures and non-zero request errors block release;
an absolute latency threshold does not.
