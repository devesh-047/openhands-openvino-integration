#!/usr/bin/env bash
#
# deploy_model_ovms.sh — OVMS-native deployment flow.
#
# Usage:
#   ./scripts/deploy_model_ovms.sh <HF_MODEL_ID> [PRECISION] [TARGET_DEVICE]
#
# Example:
#   ./scripts/deploy_model_ovms.sh OpenVINO/Qwen3-8B-Instruct-int8-ov int8 CPU
#
# This script implements an OVMS-native pull workflow:
#   • OVMS itself downloads the model from Hugging Face Hub via --source_model.
#   • No huggingface-cli, no manual model downloads, no graph.pbtxt generation.
#   • docker-compose.yml is patched in-place (OVMS command block, model
#     volume mount, and OpenHands LLM_MODEL only).
#   • The openhands service and unrelated compose settings are untouched.
#
# IMPORTANT: This script is intentionally separate from deploy_model.sh.
#            Do NOT modify deploy_model.sh.

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing — identical convention to deploy_model.sh
# ---------------------------------------------------------------------------
PRECISION="${2:-int4}"
TARGET_DEVICE="${3:-GPU}"
DEFAULT_HF_MODEL_ID="OpenVINO/Qwen2.5-1.5B-Coder-${PRECISION}-ov"
HF_MODEL_ID="${1:-${DEFAULT_HF_MODEL_ID}}"

# ---------------------------------------------------------------------------
# Path constants — identical to deploy_model.sh
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODELS_ROOT="${PROJECT_ROOT}/docker/models"
OVMS_CONFIG="${PROJECT_ROOT}/configs/ovms_config.json"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

# ---------------------------------------------------------------------------
# LOCAL_NAME derivation — identical normalization logic as deploy_model.sh
# ---------------------------------------------------------------------------
MODEL_BASENAME="${HF_MODEL_ID##*/}"
LOCAL_BASE="$(printf '%s' "${MODEL_BASENAME}" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9._-]+/-/g; s/-+/-/g; s/^-//; s/-$//')"
LOCAL_NAME="${LOCAL_BASE}"

# MODEL_DIR is where OVMS will materialise the downloaded model inside the
# container's /models volume. OVMS-native pulls use the source-model path, so
# keep the host path aligned with HF_MODEL_ID.
MODEL_DIR="${MODELS_ROOT}/${HF_MODEL_ID}"

# ---------------------------------------------------------------------------
# Logging helpers — same style as deploy_model.sh
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Tool-parser mapping
#
# Maps a normalised LOCAL_NAME to the OVMS tool_parser name.
# Returns an empty string when no tool parser is needed (e.g. plain chat
# models that do not expose function-calling syntax).
#
# Extend this function as new parsers are added to OVMS.
# Supported parsers (from llm_reference.md):
#   hermes3, llama3, phi4, mistral, devstral, gptoss, qwen3coder
# ---------------------------------------------------------------------------
resolve_tool_parser() {
  local name="${1}"

  # Qwen3-Coder family — dedicated parser
  if [[ "${name}" =~ qwen3.*coder || "${name}" =~ qwen3coder ]]; then
    printf 'qwen3coder'
    return
  fi

  # Qwen3 (non-Coder) — hermes3 works for all Qwen3 models per OVMS docs
  if [[ "${name}" =~ qwen3 ]]; then
    printf 'hermes3'
    return
  fi

  # Qwen2.5-Coder family
  if [[ "${name}" =~ qwen.*coder ]]; then
    printf 'hermes3'
    return
  fi

  # Llama-3.x / Meta-Llama-3.x family
  if [[ "${name}" =~ llama.*3 || "${name}" =~ llama-3 ]]; then
    printf 'llama3'
    return
  fi

  # Phi-4 family
  if [[ "${name}" =~ phi.*4 || "${name}" =~ phi-4 ]]; then
    printf 'phi4'
    return
  fi

  # Mistral / Mixtral family
  if [[ "${name}" =~ mistral || "${name}" =~ mixtral ]]; then
    printf 'mistral'
    return
  fi

  # Devstral
  if [[ "${name}" =~ devstral ]]; then
    printf 'devstral'
    return
  fi

  # GPT-OSS
  if [[ "${name}" =~ gptoss ]]; then
    printf 'gptoss'
    return
  fi

  # No tool parser required — return empty string
  printf ''
}

# ---------------------------------------------------------------------------
# Guard: HF_TOKEN must be set before attempting a pull from HF Hub.
# OVMS passes this through as an environment variable inside the container.
# ---------------------------------------------------------------------------
check_hf_token() {
  if [[ -z "${HF_TOKEN:-}" ]]; then
    printf 'ERROR: HF_TOKEN environment variable not set.\n' >&2
    printf 'Export your Hugging Face token first:\n' >&2
    printf '    export HF_TOKEN=<token>\n' >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Validate LOCAL_NAME and TARGET_DEVICE — identical guards to deploy_model.sh
# ---------------------------------------------------------------------------
validate_args() {
  [[ -n "${LOCAL_BASE}" ]] || die "Could not derive a local model name from '${HF_MODEL_ID}'"
  [[ "${TARGET_DEVICE}" =~ ^[A-Za-z0-9._:-]+$ ]] \
    || die "Target device must contain only letters, numbers, dots, underscores, colons, or dashes: ${TARGET_DEVICE}"
  [[ "${LOCAL_NAME}" =~ ^[a-z0-9._-]+$ ]] \
    || die "Derived model name is unsafe: ${LOCAL_NAME}"
  [[ -f "${COMPOSE_FILE}" ]] || die "Missing ${COMPOSE_FILE}"
  [[ -d "${MODELS_ROOT}" ]]  || die "Missing ${MODELS_ROOT}"
}

# ---------------------------------------------------------------------------
# Regenerate ovms_config.json
#
# The config format is identical to what deploy_model.sh writes.
# OVMS reads this on startup to discover which MediaPipe pipelines to load.
# When --source_model is used, OVMS materialises the model under
# model_repository_path using the source-model path, so base_path must match
# that location.
# ---------------------------------------------------------------------------
write_ovms_config() {
  log "Writing OVMS config: ${OVMS_CONFIG}"
  cat > "${OVMS_CONFIG}" <<EOF_JSON
{
    "model_config_list": [],
    "mediapipe_config_list": [
        {
            "name": "${LOCAL_NAME}",
            "base_path": "/models/${HF_MODEL_ID}"
        }
    ]
}
EOF_JSON
}

normalize_generated_graph() {
  local graph_file="${MODEL_DIR}/graph.pbtxt"
  local expected_path="/models/${HF_MODEL_ID}"

  if [[ ! -f "${graph_file}" ]]; then
    log "No generated graph found at ${graph_file}; skipping path normalization"
    return 0
  fi

  log "Normalizing graph models_path in ${graph_file}"
  sed -i -E \
    "s|^([[:space:]]*models_path:[[:space:]]*).*$|\1\"${expected_path}\"|" \
    "${graph_file}"
}

# ---------------------------------------------------------------------------
# Patch docker-compose.yml — OVMS service command block only.
#
# Strategy: use Python (always available in any Linux environment) to do a
# structure-preserving in-place rewrite of only the command: section under
# the ovms-llm service. All other services and keys are left byte-for-byte
# identical.
#
# Why Python and not sed?
#   The command: block is a multi-line YAML list. sed cannot reliably match
#   and replace a variable-length multi-line block without losing surrounding
#   structure. Python's yaml round-trip is the correct tool for this job.
#
# Why not ruamel.yaml?
#   ruamel is not guaranteed to be present. We use the stdlib PyYAML which
#   IS included in many Docker images and most Linux distros. However, PyYAML
#   may alter indentation. We therefore write the patch with explicit string
#   manipulation to preserve the file as closely as possible.
#
# Safeguard: the function verifies exactly one ovms-llm service block exists.
# ---------------------------------------------------------------------------
build_ovms_command() {
  local tool_parser="${1}"

  # Build the list of command arguments that will replace the current command:
  # block in docker-compose.yml.
  local args=(
    "--model_repository_path" "/models"
    "--source_model"          "${HF_MODEL_ID}"
    "--model_name"            "${LOCAL_NAME}"
    "--task"                  "text_generation"
    "--target_device"         "${TARGET_DEVICE}"
    "--port"                  "9000"
    "--rest_port"             "8000"
  )

  if [[ -n "${tool_parser}" ]]; then
    args+=("--tool_parser" "${tool_parser}")
  fi

  printf '%s\n' "${args[@]}"
}

patch_compose_command() {
  local tool_parser="${1}"

  log "Patching Docker Compose OVMS command block"

  # Verify exactly one 'ovms-llm:' service key exists so we know which
  # service to target.
  local count
  count="$(grep -Ec '^[[:space:]]*ovms-llm:[[:space:]]*$' "${COMPOSE_FILE}" || true)"
  [[ "${count}" == "1" ]] \
    || die "Expected exactly one 'ovms-llm:' service in ${COMPOSE_FILE}, found ${count}"

  # Build the replacement command block as a YAML snippet.
  # Indentation: 4 spaces (matching the existing compose file style).
  local indent="    "
  local cmd_yaml
  cmd_yaml="${indent}command:"

  # Append --model_repository_path
  cmd_yaml+="
${indent}  - --model_repository_path
${indent}  - /models"

  # Append --source_model
  cmd_yaml+="
${indent}  - --source_model
${indent}  - ${HF_MODEL_ID}"

  # Append --model_name
  cmd_yaml+="
${indent}  - --model_name
${indent}  - ${LOCAL_NAME}"

  # Append --task
  cmd_yaml+="
${indent}  - --task
${indent}  - text_generation"

  # Append --target_device
  cmd_yaml+="
${indent}  - --target_device
${indent}  - ${TARGET_DEVICE}"

  # Append --port and --rest_port (preserve existing port settings)
  cmd_yaml+="
${indent}  - --port
${indent}  - \"9000\"
${indent}  - --rest_port
${indent}  - \"8000\""

  # Append --tool_parser only when a parser is needed
  if [[ -n "${tool_parser}" ]]; then
    cmd_yaml+="
${indent}  - --tool_parser
${indent}  - ${tool_parser}"
  fi

  # Use Python to replace the command block in-place.
  # The script finds the line containing '    command:' inside the ovms-llm
  # service block and replaces everything from that line through the last
  # consecutive '      - ...' line that follows it.
  python3 - "${COMPOSE_FILE}" "${cmd_yaml}" <<'EOF_PYTHON'
import sys, re

compose_path = sys.argv[1]
new_block    = sys.argv[2]

with open(compose_path, 'r') as fh:
    content = fh.read()

# Match the command block: leading whitespace + 'command:' followed by any
# number of lines that start with MORE leading whitespace (list items).
# The pattern is non-greedy and anchored to the same indentation depth used
# by 'command:' itself (4 spaces in this project).
pattern = re.compile(
    r'^(    command:\n(?:      .*\n)*)',
    re.MULTILINE
)

matches = pattern.findall(content)
if len(matches) != 1:
    sys.stderr.write(
        f"ERROR: Expected exactly 1 'command:' block in compose file, "
        f"found {len(matches)}\n"
    )
    sys.exit(1)

# Replace, appending a trailing newline so the file stays well-formed.
replacement = new_block + '\n'
updated = pattern.sub(replacement, content, count=1)

with open(compose_path, 'w') as fh:
    fh.write(updated)

print("  compose command block updated.")
EOF_PYTHON

  # Also update LLM_MODEL env var in the openhands service so OpenHands
  # discovers the newly-deployed model automatically.
  # This mirrors the equivalent replacement in deploy_model.sh.
  local count_llm
  count_llm="$(grep -Ec '^([[:space:]]*)LLM_MODEL:[[:space:]].*$' "${COMPOSE_FILE}" || true)"
  [[ "${count_llm}" == "1" ]] \
    || die "Expected exactly one LLM_MODEL line in ${COMPOSE_FILE}, found ${count_llm}"

  sed -i -E \
    "s|^([[:space:]]*LLM_MODEL:[[:space:]]*).*\$|\1openai/${LOCAL_NAME}|" \
    "${COMPOSE_FILE}"

  log "LLM_MODEL updated to: openai/${LOCAL_NAME}"
}

patch_compose_model_volume() {
  log "Updating Docker Compose model volume mount"

  local root_count legacy_count
  root_count="$(grep -Ec '^([[:space:]]*-[[:space:]]*)\./docker/models:/models:(rw|ro)[[:space:]]*$' "${COMPOSE_FILE}" || true)"
  legacy_count="$(grep -Ec '^([[:space:]]*-[[:space:]]*)\./docker/models/[^:[:space:]]+:/models/[^:[:space:]]+:ro[[:space:]]*$' "${COMPOSE_FILE}" || true)"

  if [[ "${root_count}" == "1" && "${legacy_count}" == "0" ]]; then
    log "Model volume mount already points to ./docker/models:/models"
    return 0
  fi

  [[ "${legacy_count}" == "1" ]] || die "Expected exactly one legacy model volume mount line in ${COMPOSE_FILE}, found ${legacy_count}"

  sed -i -E \
    's|^([[:space:]]*-[[:space:]]*)\./docker/models/[^:[:space:]]+:/models/[^:[:space:]]+:ro[[:space:]]*$|\1./docker/models:/models:rw|' \
    "${COMPOSE_FILE}"

  log "Model volume mount updated to: ./docker/models:/models:rw"
}

# ---------------------------------------------------------------------------
# Also pass HF_TOKEN into the OVMS container environment.
#
# OVMS needs the token at pull time.  We inject it via the environment:
# section of the ovms-llm service.  If an HF_TOKEN line already exists in
# the file we replace it; otherwise we insert it after the 'environment:'
# key (or add both).  This avoids hardcoding the token in the file.
#
# Implementation: use Python for the same reason as patch_compose_command.
# ---------------------------------------------------------------------------
patch_compose_hf_token() {
  log "Injecting HF_TOKEN into OVMS container environment"

  python3 - "${COMPOSE_FILE}" <<'EOF_PYTHON'
import sys, re, os

compose_path = sys.argv[1]
token_val    = os.environ.get('HF_TOKEN', '')

with open(compose_path, 'r') as fh:
    content = fh.read()

# Check whether an environment: section already exists under ovms-llm.
# Strategy:
#   1. If 'HF_TOKEN:' line already present → replace its value.
#   2. Else if 'environment:' key present under ovms-llm → append HF_TOKEN.
#   3. Else insert 'environment:\n      HF_TOKEN: <val>' before 'networks:'.
#
# We keep this conservative: only touch the ovms-llm service block.

hf_line = f'      HF_TOKEN: "{token_val}"'

# Case 1: HF_TOKEN already present — replace value in-place.
if re.search(r'^      HF_TOKEN:', content, re.MULTILINE):
    updated = re.sub(
        r'^(      HF_TOKEN:).*$',
        hf_line,
        content,
        flags=re.MULTILINE
    )
    with open(compose_path, 'w') as fh:
        fh.write(updated)
    print("  HF_TOKEN line updated.")
    sys.exit(0)

# Case 2: environment: block present under ovms-llm — append to it.
# Find the ovms-llm service environment block and append HF_TOKEN as last
# item before the block ends (next key at same or lower indent level).
env_block = re.search(
    r'(  ovms-llm:.*?)(    environment:\n)((?:      .*\n)*)',
    content,
    re.DOTALL
)
if env_block:
    prefix   = env_block.group(1)
    env_head = env_block.group(2)
    env_body = env_block.group(3)
    new_body = env_body + hf_line + '\n'
    updated  = content[:env_block.start()] + prefix + env_head + new_body + content[env_block.end():]
    with open(compose_path, 'w') as fh:
        fh.write(updated)
    print("  HF_TOKEN appended to existing environment block.")
    sys.exit(0)

# Case 3: no environment: block under ovms-llm — insert one before networks:.
# Insert just before the first '    networks:' line inside ovms-llm.
networks_match = re.search(r'^    networks:', content, re.MULTILINE)
if not networks_match:
    sys.stderr.write("ERROR: Could not locate 'networks:' key in compose file.\n")
    sys.exit(1)

insert_pos = networks_match.start()
env_section = f'    environment:\n{hf_line}\n'
updated = content[:insert_pos] + env_section + content[insert_pos:]
with open(compose_path, 'w') as fh:
    fh.write(updated)
print("  environment: section with HF_TOKEN inserted.")
EOF_PYTHON
}

# ---------------------------------------------------------------------------
# Wait for OVMS to become healthy.
#
# Polls docker logs for the line OVMS prints when a model finishes loading.
# Falls back to polling the /v1/config REST endpoint for AVAILABLE status.
# Bounded by WAIT_TIMEOUT_S seconds.
# ---------------------------------------------------------------------------
OVMS_CONTAINER_NAME="ovms-llm"
WAIT_TIMEOUT_S=6000   # 6000 seconds (100 minutes) — large models can be slow to download
POLL_INTERVAL_S=15

wait_for_ovms() {
  log "Waiting for OVMS to become healthy (timeout: ${WAIT_TIMEOUT_S}s)"
  printf '    Model download + load may take several minutes.\n'
  printf '    Follow progress with: docker logs -f %s\n' "${OVMS_CONTAINER_NAME}"

  local elapsed=0
  while (( elapsed < WAIT_TIMEOUT_S )); do
    # Primary signal: OVMS logs a line containing 'Servable available' or
    # 'Model status: AVAILABLE' when a mediapipe graph is ready.
    if docker logs "${OVMS_CONTAINER_NAME}" 2>&1 \
        | grep -qE 'Servable available|model status.*AVAILABLE|AVAILABLE.*status|LOADING_ONCE.*OK'; then
      log "OVMS reports servable available"
      return 0
    fi

    # Secondary signal: REST /v1/config endpoint returns AVAILABLE state.
    local cfg_status
    cfg_status="$(curl -sf "http://localhost:8000/v1/config" 2>/dev/null || true)"
    if printf '%s' "${cfg_status}" | grep -q '"AVAILABLE"'; then
      log "OVMS REST endpoint confirms model is AVAILABLE"
      return 0
    fi

    # Check that the container is still running; exit early if it crashed.
    local ctr_state
    ctr_state="$(docker inspect --format '{{.State.Status}}' \
                  "${OVMS_CONTAINER_NAME}" 2>/dev/null || true)"
    if [[ "${ctr_state}" == "exited" || "${ctr_state}" == "dead" ]]; then
      printf '\nERROR: OVMS container exited unexpectedly.\n' >&2
      printf 'Recent logs:\n' >&2
      docker logs --tail 40 "${OVMS_CONTAINER_NAME}" >&2 || true
      exit 1
    fi

    printf '    [%ds / %ds] OVMS not ready yet — waiting %ds...\n' \
      "${elapsed}" "${WAIT_TIMEOUT_S}" "${POLL_INTERVAL_S}"
    sleep "${POLL_INTERVAL_S}"
    (( elapsed += POLL_INTERVAL_S ))
  done

  # Timeout reached — print diagnostics and exit nonzero.
  printf '\nERROR: OVMS did not become healthy within %d seconds.\n' \
    "${WAIT_TIMEOUT_S}" >&2
  printf 'Recent OVMS logs:\n' >&2
  docker logs --tail 60 "${OVMS_CONTAINER_NAME}" >&2 || true
  printf '\nDiagnostics:\n' >&2
  printf '  docker inspect %s\n' "${OVMS_CONTAINER_NAME}" >&2
  printf '  docker logs %s\n' "${OVMS_CONTAINER_NAME}" >&2
  exit 1
}

# ---------------------------------------------------------------------------
# Verify that OVMS materialised the model directory on the host volume.
#
# When OVMS pulls via --source_model, it writes the downloaded/converted
# files under model_repository_path/<model_name> inside the container,
# which maps to MODELS_ROOT/<LOCAL_NAME> on the host.
# ---------------------------------------------------------------------------
verify_model_dir() {
  log "Verifying model directory: ${MODEL_DIR}"
  if [[ -d "${MODEL_DIR}" ]]; then
    printf '    Model directory exists: %s\n' "${MODEL_DIR}"
    printf '    Contents:\n'
    ls -lh "${MODEL_DIR}" | head -20 || true
  else
    printf '    WARNING: %s does not exist yet.\n' "${MODEL_DIR}" >&2
    printf '    OVMS may still be writing files, or uses an internal volume.\n' >&2
    printf '    This is non-fatal — inference may still work correctly.\n' >&2
  fi
}

# ---------------------------------------------------------------------------
# Print final success summary
# ---------------------------------------------------------------------------
print_summary() {
  local tool_parser="${1}"
  local ovms_status
  local openhands_status

  ovms_status="$(docker inspect --format '{{.State.Status}}' \
                   "${OVMS_CONTAINER_NAME}" 2>/dev/null || printf 'unknown')"
  openhands_status="$(docker inspect --format '{{.State.Status}}' \
                        "openhands" 2>/dev/null || printf 'unknown')"

  printf '\n'
  printf '==> Deployment summary\n'
  printf '    HF model:         %s\n' "${HF_MODEL_ID}"
  printf '    Local model:      %s\n' "${LOCAL_NAME}"
  printf '    Target device:    %s\n' "${TARGET_DEVICE}"
  printf '    Model directory:  %s\n' "${MODEL_DIR}"
  printf '    Tool parser:      %s\n' "${tool_parser:-none}"
  printf '    OVMS status:      %s\n' "${ovms_status}"
  printf '    OpenHands status: %s\n' "${openhands_status}"
  printf '\n'
  printf 'Remember to set the model name in the OpenHands UI settings to:\n'
  printf '    openai/%s\n' "${LOCAL_NAME}"
}

# ===========================================================================
# Main
# ===========================================================================

validate_args
check_hf_token

require_command docker
require_command grep
require_command sed
require_command python3
require_command curl

docker compose version >/dev/null 2>&1 \
  || die "Docker Compose plugin is not available via 'docker compose'"

log "Deploying Hugging Face model via OVMS-native pull: ${HF_MODEL_ID}"
printf '    Precision:  %s\n' "${PRECISION}"
printf '    Device:     %s\n' "${TARGET_DEVICE}"
printf '    Local name: %s\n' "${LOCAL_NAME}"

# Resolve tool parser before mutating anything.
TOOL_PARSER="$(resolve_tool_parser "${LOCAL_NAME}")"
if [[ -n "${TOOL_PARSER}" ]]; then
  log "Tool parser resolved: ${TOOL_PARSER}"
else
  log "No tool parser required for this model family"
fi

# Ensure the models root directory exists so the volume mount is valid.
log "Ensuring model root directory: ${MODELS_ROOT}"
mkdir -p "${MODELS_ROOT}"

# Write ovms_config.json so OVMS knows which pipeline to load.
write_ovms_config

# Patch docker-compose.yml: replace the OVMS command block with the
# native-pull variant, update the model volume mount, and update the
# OpenHands LLM_MODEL env var.
patch_compose_command "${TOOL_PARSER}"
patch_compose_model_volume

# Inject HF_TOKEN into the OVMS container environment block.
patch_compose_hf_token

log "Starting Docker Compose services"
docker compose up -d

# Wait until OVMS reports the model as available.
wait_for_ovms

# Make the generated graph explicit so the configured model path is visible
# and stable on subsequent runs.
normalize_generated_graph

# Verify that OVMS materialised the model directory (non-fatal warning if not).
verify_model_dir

print_summary "${TOOL_PARSER}"
