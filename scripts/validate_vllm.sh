#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=${IMAGE:-vllm/vllm-openai:v0.10.2}
MODEL=${MODEL:-Qwen/Qwen2.5-0.5B-Instruct}
MODEL_REVISION=${MODEL_REVISION:-7ae557604adf67be50417f59c2c2f167def9a775}
BACKEND_PORT=${BACKEND_PORT:-18000}
BACKEND_URL=http://127.0.0.1:$BACKEND_PORT
OUTPUT=${OUTPUT:-$ROOT/docs/validation/vllm-v0.12.0.json}
PYTHON=${PYTHON:-python3}
CONTAINER=meridian-vllm-validation

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

command -v docker >/dev/null 2>&1 || {
    printf '%s\n' "docker is required" >&2
    exit 1
}
command -v curl >/dev/null 2>&1 || {
    printf '%s\n' "curl is required" >&2
    exit 1
}

cleanup
docker run --detach --name "$CONTAINER" --gpus all \
    --ipc=host \
    -p "127.0.0.1:$BACKEND_PORT:8000" \
    -v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface" \
    "$IMAGE" \
    --model "$MODEL" \
    --revision "$MODEL_REVISION" \
    --served-model-name "$MODEL" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.75}" \
    --max-model-len "${MAX_MODEL_LEN:-4096}"

attempts=0
until curl --fail --silent "$BACKEND_URL/v1/models" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 300 ]; then
        docker logs "$CONTAINER" >&2
        exit 1
    fi
    sleep 2
done

"$PYTHON" "$ROOT/scripts/validate_real_backend.py" \
    --engine vllm \
    --engine-version "${IMAGE##*:}" \
    --backend-url "$BACKEND_URL" \
    --model "$MODEL" \
    --model-revision "$MODEL_REVISION" \
    --requests "${REQUESTS:-30}" \
    --concurrency "${CONCURRENCY:-1}" \
    --warmup "${WARMUP:-3}" \
    --output "$OUTPUT"
