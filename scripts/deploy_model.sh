#!/usr/bin/env bash
set -euo pipefail

PRECISION="${2:-int4}"
TARGET_DEVICE="${3:-GPU}"
DEFAULT_HF_MODEL_ID="OpenVINO/Qwen2.5-1.5B-Coder-${PRECISION}-ov"
HF_MODEL_ID="${1:-${DEFAULT_HF_MODEL_ID}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODELS_ROOT="${PROJECT_ROOT}/docker/models"
OVMS_CONFIG="${PROJECT_ROOT}/configs/ovms_config.json"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

MODEL_BASENAME="${HF_MODEL_ID##*/}"
LOCAL_BASE="$(printf '%s' "${MODEL_BASENAME}" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9._-]+/-/g; s/-+/-/g; s/^-//; s/-$//')"
LOCAL_NAME="${LOCAL_BASE}"
MODEL_DIR="${MODELS_ROOT}/${LOCAL_NAME}"

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

model_dir_has_openvino_model() {
  [[ -f "${MODEL_DIR}/openvino_model.xml" && -f "${MODEL_DIR}/openvino_model.bin" ]]
}

replace_one_line() {
  local description="$1"
  local pattern="$2"
  local replacement="$3"

  local count
  count="$(grep -Ec "${pattern}" "${COMPOSE_FILE}" || true)"
  [[ "${count}" == "1" ]] || die "Expected exactly one ${description} line in ${COMPOSE_FILE}, found ${count}"

  sed -i -E "s|${pattern}|${replacement}|" "${COMPOSE_FILE}"
}

[[ -n "${LOCAL_BASE}" ]] || die "Could not derive a local model name from '${HF_MODEL_ID}'"
[[ "${TARGET_DEVICE}" =~ ^[A-Za-z0-9._:-]+$ ]] || die "Target device must contain only letters, numbers, dots, underscores, colons, or dashes: ${TARGET_DEVICE}"
[[ "${LOCAL_NAME}" =~ ^[a-z0-9._-]+$ ]] || die "Derived model name is unsafe: ${LOCAL_NAME}"
[[ -f "${COMPOSE_FILE}" ]] || die "Missing ${COMPOSE_FILE}"
[[ -d "${MODELS_ROOT}" ]] || die "Missing ${MODELS_ROOT}"

require_command docker
require_command grep
require_command sed
require_command tr

docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is not available via 'docker compose'"

log "Deploying Hugging Face model: ${HF_MODEL_ID}"
printf '    Precision: %s\n' "${PRECISION}"
printf '    Device:    %s\n' "${TARGET_DEVICE}"
printf '    Local name:%s\n' " ${LOCAL_NAME}"

log "Creating model directory: ${MODEL_DIR}"
mkdir -p "${MODEL_DIR}"

if model_dir_has_openvino_model; then
  log "Reusing existing OpenVINO model files in ${MODEL_DIR}"
else
  require_command huggingface-cli

  log "Downloading pre-exported model from Hugging Face Hub"
  huggingface-cli download "${HF_MODEL_ID}" \
    --local-dir "${MODEL_DIR}" \
    --local-dir-use-symlinks False
fi

log "Writing MediaPipe graph: ${MODEL_DIR}/graph.pbtxt"
cat > "${MODEL_DIR}/graph.pbtxt" <<EOF_GRAPH
input_stream: "HTTP_REQUEST_PAYLOAD:input"
output_stream: "HTTP_RESPONSE_PAYLOAD:output"

node: {
  name: "LLMExecutor"
  calculator: "HttpLLMCalculator"
  input_stream: "LOOPBACK:loopback"
  input_stream: "HTTP_REQUEST_PAYLOAD:input"
  input_side_packet: "LLM_NODE_RESOURCES:llm"
  output_stream: "LOOPBACK:loopback"
  output_stream: "HTTP_RESPONSE_PAYLOAD:output"
  input_stream_info: {
    tag_index: 'LOOPBACK:0',
    back_edge: true
  }
  node_options: {
      [type.googleapis.com / mediapipe.LLMCalculatorOptions]: {
          models_path: "/models/${LOCAL_NAME}"
          cache_size: 1
          max_num_batched_tokens: 2048
          enable_prefix_caching: true
          device: "${TARGET_DEVICE}"
      }
  }
  input_stream_handler {
    input_stream_handler: "SyncSetInputStreamHandler",
    options {
      [mediapipe.SyncSetInputStreamHandlerOptions.ext] {
        sync_set {
          tag_index: "LOOPBACK:0"
        }
      }
    }
  }
}
EOF_GRAPH

log "Updating OVMS config: ${OVMS_CONFIG}"
cat > "${OVMS_CONFIG}" <<EOF_JSON
{
    "model_config_list": [],
    "mediapipe_config_list": [
        {
            "name": "${LOCAL_NAME}",
            "base_path": "/models/${LOCAL_NAME}"
        }
    ]
}
EOF_JSON

log "Updating Docker Compose model settings"
replace_one_line \
  "LLM_MODEL environment" \
  '^([[:space:]]*LLM_MODEL:[[:space:]]*).*$' \
  "\\1openai/${LOCAL_NAME}"

replace_one_line \
  "model volume mount" \
  '^([[:space:]]*-[[:space:]]*)\./docker/models/[^:[:space:]]+:/models/[^:[:space:]]+:ro[[:space:]]*$' \
  "\\1./docker/models/${LOCAL_NAME}:/models/${LOCAL_NAME}:ro"

log "Restarting Docker Compose services"
docker compose down
docker compose up -d

log "Deployment complete"
printf 'Remember to manually update the model name in the OpenHands UI settings to: openai/%s\n' "${LOCAL_NAME}"
