#!/usr/bin/env bash
#
# Start OpenHands configured to use the local OVMS container.
# Automatically detects the OVMS container IP and injects settings so
# browser-cached settings can never override the correct endpoint.

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

OPENHANDS_IMAGE="${OPENHANDS_IMAGE:-ghcr.io/all-hands-ai/openhands:latest}"
OPENHANDS_PORT="${OPENHANDS_PORT:-3000}"
OPENHANDS_CONTAINER="${OPENHANDS_CONTAINER:-openhands}"
OVMS_CONTAINER="${OVMS_CONTAINER:-ovms-llm}"
OVMS_REST_PORT="${OVMS_REST_PORT:-8000}"
LLM_MODEL="${LLM_MODEL:-openai/qwen2.5-0.5b-instruct}"
LLM_API_KEY="${LLM_API_KEY:-unused}"

# Settings directory mounted into the container so settings always persist
# with the correct OVMS IP across restarts.
SETTINGS_DIR="${HOME}/.openhands-ovms"
SETTINGS_FILE="${SETTINGS_DIR}/settings.json"

# ---------------------------------------------------------------------------
# 1. Resolve OVMS container IP
# ---------------------------------------------------------------------------
OVMS_IP=$(docker inspect "$OVMS_CONTAINER" \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' 2>/dev/null \
    | tr ' ' '\n' | grep -v '^$' | head -1)

if [[ -z "$OVMS_IP" ]]; then
    echo "ERROR: Could not determine IP for container '$OVMS_CONTAINER'." >&2
    echo "Make sure OVMS is running: bash scripts/deploy_ovms.sh" >&2
    exit 1
fi

LLM_BASE_URL="http://${OVMS_CONTAINER}:${OVMS_REST_PORT}/v3"

echo "OVMS container : $OVMS_CONTAINER"
echo "OVMS IP        : $OVMS_IP"
echo "LLM base URL   : $LLM_BASE_URL"
echo "LLM model      : $LLM_MODEL"

# ---------------------------------------------------------------------------
# 2. Write settings.json before container starts (avoids browser override)
# ---------------------------------------------------------------------------
mkdir -p "$SETTINGS_DIR"
cat > "$SETTINGS_FILE" <<EOF
{
  "language": "en",
  "agent": "CodeActAgent",
  "max_iterations": null,
  "security_analyzer": "llm",
  "confirmation_mode": false,
  "llm_model": "${LLM_MODEL}",
  "llm_api_key": "${LLM_API_KEY}",
  "llm_base_url": "${LLM_BASE_URL}",
  "remote_runtime_resource_factor": 1,
  "secrets_store": {"provider_tokens": {}},
  "enable_default_condenser": true,
  "enable_sound_notifications": false,
  "enable_proactive_conversation_starters": false,
  "enable_solvability_analysis": false,
  "user_consents_to_analytics": true,
  "sandbox_base_container_image": null,
  "sandbox_runtime_container_image": null,
  "mcp_config": {"sse_servers": [], "stdio_servers": [], "shttp_servers": []},
  "search_api_key": null,
  "sandbox_api_key": null,
  "max_budget_per_task": null,
  "condenser_max_size": 120,
  "email": null,
  "email_verified": null,
  "git_user_name": "openhands",
  "git_user_email": "openhands@all-hands.dev"
}
EOF
echo "Settings written to: $SETTINGS_FILE"

# ---------------------------------------------------------------------------
# 3. Stop and remove existing container
# ---------------------------------------------------------------------------
if docker ps -a --format '{{.Names}}' | grep -q "^${OPENHANDS_CONTAINER}$"; then
    echo "Removing existing container: $OPENHANDS_CONTAINER"
    docker stop "$OPENHANDS_CONTAINER" >/dev/null 2>&1 || true
    docker rm   "$OPENHANDS_CONTAINER" >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# 4. Start OpenHands with settings mounted
# ---------------------------------------------------------------------------
echo "Cleaning up orphaned runtime containers to free memory..."
docker rm -f $(docker ps -a -q -f name=openhands-runtime) 2>/dev/null || true

echo "Starting OpenHands..."
docker run -d \
    --name "$OPENHANDS_CONTAINER" \
    --network ovms-net \
    -p "${OPENHANDS_PORT}:3000" \
    -e LLM_BASE_URL="$LLM_BASE_URL" \
    -e LLM_MODEL="$LLM_MODEL" \
    -e LLM_API_KEY="$LLM_API_KEY" \
    -e LITELLM_LOG="DEBUG" \
    -e SANDBOX_DOCKER_ARGS="--memory=1536m --memory-swap=1536m" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${SETTINGS_DIR}:/.openhands" \
    "$OPENHANDS_IMAGE"

echo ""
echo "OpenHands is running at http://localhost:${OPENHANDS_PORT}"
echo "Open the URL, start a new conversation, and wait for 'Microagent ready'."
