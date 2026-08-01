#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKEND_URL=${BACKEND_URL:-http://127.0.0.1:11434}
MODEL=${MODEL:-qwen2.5:0.5b}
OUTPUT=${OUTPUT:-$ROOT/docs/validation/ollama-v0.12.0.json}
PYTHON=${PYTHON:-python3}
OLLAMA_PID=

cleanup() {
    if [ -n "$OLLAMA_PID" ]; then
        kill "$OLLAMA_PID" 2>/dev/null || true
        wait "$OLLAMA_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

command -v ollama >/dev/null 2>&1 || {
    printf '%s\n' "ollama is required: https://ollama.com/download" >&2
    exit 1
}
command -v curl >/dev/null 2>&1 || {
    printf '%s\n' "curl is required" >&2
    exit 1
}

if ! curl --fail --silent "$BACKEND_URL/v1/models" >/dev/null 2>&1; then
    ollama serve >"${TMPDIR:-/tmp}/meridian-ollama.log" 2>&1 &
    OLLAMA_PID=$!
    attempts=0
    until curl --fail --silent "$BACKEND_URL/v1/models" >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 60 ]; then
            printf '%s\n' "Ollama did not become ready; see ${TMPDIR:-/tmp}/meridian-ollama.log" >&2
            exit 1
        fi
        sleep 1
    done
fi

ollama pull "$MODEL"
ENGINE_VERSION=$(ollama --version 2>&1 | sed -n 's/.*version is //p' | tail -n 1)
ENGINE_VERSION=${ENGINE_VERSION:-unknown}

"$PYTHON" "$ROOT/scripts/validate_real_backend.py" \
    --engine ollama \
    --engine-version "$ENGINE_VERSION" \
    --backend-url "$BACKEND_URL" \
    --model "$MODEL" \
    --requests "${REQUESTS:-30}" \
    --concurrency "${CONCURRENCY:-1}" \
    --warmup "${WARMUP:-3}" \
    --output "$OUTPUT"
