#!/usr/bin/env bash
#
# Deploy OpenVINO Model Server with LLM serving configuration.
# Requires Docker and a pre-converted OpenVINO IR model directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

source_env() {
    local env_file="${PROJECT_ROOT}/configs/.env"
    if [[ -f "$env_file" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$env_file"
        set +a
    fi
}

source_env

OVMS_IMAGE="${OVMS_IMAGE:-openvino/model_server:latest}"
OVMS_REST_PORT="${OVMS_REST_PORT:-8000}"
OVMS_GRPC_PORT="${OVMS_GRPC_PORT:-9000}"
MODEL_DIR="${MODEL_DIR:-${PROJECT_ROOT}/docker/models/tiny-llama-1.1b-chat}"
CONFIG_FILE="${PROJECT_ROOT}/configs/ovms_config.json"
CONTAINER_NAME="${CONTAINER_NAME:-ovms-llm}"

if [[ ! -d "$MODEL_DIR" ]]; then
    echo "ERROR: Model directory not found: $MODEL_DIR" >&2
    echo "Convert and place the OpenVINO IR model before deploying." >&2
    exit 1
fi

if [[ ! -f "${MODEL_DIR}/graph.pbtxt" ]]; then
    echo "ERROR: graph.pbtxt not found in $MODEL_DIR" >&2
    echo "The model directory must contain graph.pbtxt for LLM pipeline serving." >&2
    exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: OVMS config not found: $CONFIG_FILE" >&2
    exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Stopping existing container: $CONTAINER_NAME"
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

echo "Starting OVMS container: $CONTAINER_NAME"
echo "  Image:     $OVMS_IMAGE"
echo "  REST port: $OVMS_REST_PORT"
echo "  gRPC port: $OVMS_GRPC_PORT"
echo "  Model dir: $MODEL_DIR"

docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${OVMS_REST_PORT}:8000" \
    -p "${OVMS_GRPC_PORT}:9000" \
    -v "$(realpath "$MODEL_DIR"):/models/tiny-llama-1.1b-chat:ro" \
    -v "$(realpath "$CONFIG_FILE"):/config/ovms_config.json:ro" \
    "$OVMS_IMAGE" \
    --config_path /config/ovms_config.json \
    --port 9000 \
    --rest_port 8000

echo "Waiting for OVMS LLM graph to initialize (this may take 30-60 seconds)..."
sleep 10

# Poll /v1/config until the model reports AVAILABLE state.
# /v2/health/ready returns 200 immediately on server start before the model loads.
MAX_RETRIES=18
for i in $(seq 1 $MAX_RETRIES); do
    status=$(curl -sf "http://localhost:${OVMS_REST_PORT}/v1/config" 2>/dev/null || true)
    if echo "$status" | grep -q '"AVAILABLE"'; then
        echo "OVMS is ready. Model status: AVAILABLE"
        exit 0
    fi
    echo "  Attempt $i/$MAX_RETRIES: model not available yet..."
    sleep 10
done

echo "ERROR: OVMS failed to become ready within expected time." >&2
echo "Check container logs: docker logs $CONTAINER_NAME" >&2
exit 1
