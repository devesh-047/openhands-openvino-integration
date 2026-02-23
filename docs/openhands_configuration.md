# OpenHands Configuration for OVMS

## Overview

OpenHands uses a TOML configuration file and environment variables to specify the LLM backend. To connect OpenHands to OVMS, three parameters must be set correctly.

## Required Configuration

### Option 1: Environment Variables

When running OpenHands via Docker:

```bash
docker run -d \
    --name openhands \
    -p 3000:3000 \
    -e LLM_BASE_URL="http://host.docker.internal:8000/v3" \
    -e LLM_MODEL="tiny-llama-1.1b-chat" \
    -e LLM_API_KEY="unused" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    ghcr.io/all-hands-ai/openhands:latest
```

### Option 2: config.toml

Create or modify `~/.openhands/config.toml`:

```toml
[llm]
model = "tiny-llama-1.1b-chat"
base_url = "http://host.docker.internal:8000/v3"
api_key = "unused"
```

## Parameter Details

### `LLM_BASE_URL`

The base URL must point to the OVMS REST endpoint including the `/v3` path prefix. OVMS uses `/v3` rather than the standard `/v1` used by OpenAI.

If both OVMS and OpenHands run as Docker containers on the same host:
- Use `http://host.docker.internal:8000/v3` (Docker Desktop on macOS/Windows).
- Use `http://172.17.0.1:8000/v3` (Docker default bridge on Linux).
- Do not use `http://localhost:8000/v3` -- this resolves to the OpenHands container itself.

If OpenHands runs directly on the host (not in Docker):
- Use `http://localhost:8000/v3`.

### `LLM_MODEL`

Must exactly match the model name specified in `ovms_config.json`. Case-sensitive. There is no model discovery endpoint; the name must be known in advance.

### `LLM_API_KEY`

OVMS does not require an API key. However, OpenHands requires the field to be non-empty. Set it to any non-empty string such as `"unused"`.

## Verification Steps

After configuring OpenHands:

1. Open the OpenHands web UI at `http://localhost:3000`.
2. Open settings (gear icon).
3. Confirm that the LLM provider shows the configured model.
4. Start a new workspace and send a test message.
5. Verify that a response is received from OVMS.

To confirm the connection independently:

```bash
curl -s http://localhost:8000/v3/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "tiny-llama-1.1b-chat",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 32
    }' | python3 -m json.tool
```

## Troubleshooting

### "Connection refused" in OpenHands

- Verify OVMS is running: `docker ps | grep ovms`
- Verify the port mapping: `curl http://localhost:8000/v2/health/ready`
- If using Docker-to-Docker networking, confirm `host.docker.internal` resolves correctly.

### "Model not found" error

- Verify `LLM_MODEL` matches the model name in `ovms_config.json` exactly.
- Check OVMS logs: `docker logs ovms-llm`

### Empty or malformed responses

- Check OVMS version compatibility. LLM serving is available from OVMS 2024.1 onwards.
- Verify the model was converted correctly and the IR files are present in the mounted volume.

### Slow responses

- Expected behavior on CPU with larger models. TinyLlama 1.1B INT8 typically responds within 5-15 seconds for short prompts on a modern CPU.
- Set `max_tokens` to a reasonable limit to avoid long generation times.
