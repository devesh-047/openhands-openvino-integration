# Known Limitations

## OVMS LLM Serving

### No function calling or tool use

OVMS does not implement the `functions` or `tools` fields in the chat completions API. OpenHands features that depend on structured tool invocation via the OpenAI function calling protocol will not work. The agent must rely on prompt-based instruction parsing instead.

### No JSON mode

The `response_format: { type: "json_object" }` parameter is not supported. Agents that expect guaranteed JSON output must handle parsing failures gracefully.

### Single completion only

The `n` parameter is ignored. Only one completion choice is returned per request. Agents that use multiple completions for voting or ranking strategies cannot do so through this endpoint.

### No log probabilities

The `logprobs` field is not available. Confidence scoring based on token probabilities is not possible.

### No deterministic generation

The `seed` parameter is not supported. Results are not reproducible across identical requests even with `temperature=0`.

## Streaming

### Partial `usage` reporting

Streamed responses may not include token usage statistics. Clients that track token consumption per request will need to estimate or disable tracking for streamed interactions.

### No mid-stream cancellation

Closing the HTTP connection during streaming does not reliably cancel the in-progress generation on the server side. The server may continue to generate tokens, consuming resources.

## Model Constraints

### Context window

The effective context window depends on the model and the `cache_size` parameter in `graph.pbtxt`. TinyLlama 1.1B supports a 2048-token context. Long multi-turn conversations will exceed this limit, resulting in truncated or incoherent responses.

### Model quality

Small quantized models (INT8 TinyLlama) produce lower quality outputs compared to larger models. For actual coding tasks through OpenHands, a model with at least 7B parameters is recommended. The TinyLlama configuration is intended for integration testing, not production use.

### Chat template

OVMS applies the chat template embedded in the model's tokenizer configuration. If the template does not match what the client expects, system prompts or role-based instructions may not be interpreted correctly.

## Networking

### Docker networking in WSL

Docker containers running in WSL2 have specific networking constraints:

- `localhost` inside a container refers to the container, not the WSL host.
- `host.docker.internal` may require Docker Desktop or explicit `--add-host` configuration.
- Port forwarding from WSL2 to Windows requires additional configuration for external access.

### No TLS

OVMS does not serve HTTPS by default. In environments where OpenHands enforces HTTPS for API endpoints, a reverse proxy (e.g., nginx) must be placed in front of OVMS.

## OpenHands Compatibility

### Agent-specific behavior

Different OpenHands agents (CodeAct, Browsing, etc.) issue different types of requests. Some agents may send requests with unsupported parameters, leading to errors or degraded behavior. Testing should cover the specific agent type intended for use.

### Retry and timeout handling

OpenHands may retry failed requests with its own backoff strategy. Combined with OVMS inference latency on CPU, this can lead to queued requests and increased memory pressure. Monitor container resource usage during extended sessions.

### Model metadata assumptions

OpenHands may query model metadata endpoints that OVMS does not implement. This can cause warnings in the OpenHands UI but typically does not block functionality.
