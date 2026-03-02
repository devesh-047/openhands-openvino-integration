# Integration Observations

This document captures performance observations, model comparisons, and end-to-end behavior when running OpenHands connected to OpenVINO Model Server (OVMS). It is intended to give an honest, data-driven picture of the current state of this integration.

---

## Hardware and Environment

| Item | Spec |
|---|---|
| Machine | Windows 11, WSL2 (Ubuntu 20.04) |
| CPU | Intel (12 logical cores visible to Docker) |
| RAM (Physical) | 16 GB total |
| RAM (WSL2 Limit) | 8 GB (via `.wslconfig`) |
| Swap | 8 GB |
| GPU | None (CPU-only inference) |
| OVMS Image | `openvino/model_server:latest` (GPU variant — includes LLM extensions) |
| OpenHands Image | `ghcr.io/all-hands-ai/openhands:latest` |
| OVMS Version | 2026.0.0 |

---

## Models Tested

### Model 1: TinyLlama-1.1B-Chat-v1.0

| Property | Value |
|---|---|
| Parameters | 1.1 Billion |
| Format | OpenVINO IR, INT4 (converted via `optimum-cli`) |
| Context Window | 2,048 tokens |
| Disk Size (after conversion) | ~700 MB |

**Result: ❌ Failed — Context Window Too Small**

OpenHands' `CodeActAgent` sends a system prompt of approximately 2,000–3,000 tokens on every single request. This prompt contains the agent's tool definitions, workspace instructions, and safety guidelines. With a 2,048-token limit, even the system prompt alone fills the entire context before any user message can be included. OVMS returned errors or empty outputs on every attempt.

**Lesson learned:** A minimum of ~4,000 tokens of *usable* context (after the system prompt) is required for any practical use of OpenHands.

---

### Model 2: Phi-3.5-mini-instruct (3.8B)

| Property | Value |
|---|---|
| Parameters | 3.82 Billion |
| Format | OpenVINO IR, INT4 (pre-converted from OpenVINO HuggingFace Hub) |
| Context Window | 128,000 tokens |
| Disk Size | ~2.08 GB (`.bin` file only) |
| Peak OVMS Memory Usage | **~6.3 GB** (measured via `docker stats`) |

**Result: ❌ Failed — OOM Crash on Every Inference**

While the model successfully loaded and OVMS reached `AVAILABLE` status, every inference attempt crashed OVMS with exit code 137 (`SIGKILL`). The exact memory breakdown at the point of failure:

| Component | ~RAM Used |
|---|---|
| OVMS (base + model weights) | 5.3 GB |
| OpenHands runtime sandbox | 1.5–1.8 GB |
| OS kernel + Docker daemon | 0.8 GB |
| **Total** | **~7.6–8.0 GB** |

With the WSL2 limit at 8 GB, the system had zero headroom for the inference forward-pass compute buffers. The OOM killer killed the OVMS process on every single request.

**Approaches tried to resolve but failed:**
- Setting `cache_size: 0` in `graph.pbtxt` → OVMS hung indefinitely (no KV cache = cannot generate tokens)
- Setting `cache_size: 0.5` → Protobuf parse error (must be integer, not float)
- Adding 8 GB swap → Did not help; the model weights themselves + inference buffers together exceed physical RAM, and swap latency was too high for the OOM killer's threshold
- Capping the OpenHands sandbox at 1.5 GB → Still left insufficient headroom for OVMS inference buffers

**Conclusion:** The Phi-3.5-mini model is fundamentally too large for the 8 GB WSL2 environment when used alongside the OpenHands sandbox container.

---

### Model 3: Qwen2.5-0.5B-Instruct ✅ (Current)

| Property | Value |
|---|---|
| Parameters | 0.5 Billion |
| Format | OpenVINO IR, INT4 (converted via `optimum-cli`) |
| Context Window | 32,768 tokens |
| Disk Size (after conversion) | ~400–500 MB |
| Peak OVMS Memory Usage | **~1.5–2.0 GB** |

**Result: ✅ Loads Successfully — Inference Works — Outputs Are Hallucinated for Complex Prompts**

This model successfully loads, serves requests over `/v3/chat/completions`, and returns generated tokens within the WSL2 memory budget. Memory breakdown at runtime:

| Component | ~RAM Used |
|---|---|
| OVMS (base + model weights + KV cache) | 1.5–2.0 GB |
| OpenHands runtime sandbox | 1.5 GB |
| OS kernel + Docker daemon | 0.8 GB |
| **Total** | **~3.8–4.3 GB** |

This fits comfortably within the 8 GB limit, with ~3.7 GB free for buffering.

---

## End-to-End Performance Observations

### Direct Inference (curl / Python test — Qwen2.5-0.5B)

| Metric | Observed |
|---|---|
| Time to first token | ~3–5 seconds (CPU) |
| Tokens per second | ~3–5 tok/s |
| Response for "say hi" with max_tokens=5 | Returned within ~8–10 seconds |
| HTTP Status | 200 OK |
| KV Cache Used | ~9.1–9.3% of pre-allocated 1 GB |

The model successfully generates text at approximately 3–5 tokens/second on CPU, which is expected for a 0.5B INT4 model using OpenVINO's CPU inference backend.

### OpenHands Agent (Qwen2.5-0.5B via CodeActAgent)

| Metric | Observed |
|---|---|
| Time to first response | 3–7 minutes |
| OVMS scheduled requests visible | Yes (`Scheduled requests: 1`) |
| KV Cache utilization growth | Gradual increase from 9.1% → 9.3%+ over minutes |
| Response quality for "say hi" | ❌ Returned a hallucinated bash function block |
| Response quality for "write a Python script to print 1 to 10" | ❌ Returned hallucinated bash snippet (`find` command) |

**Why the response time is 3–7 minutes:**
OpenHands sends the entire agent system prompt (~2,000–3,000 tokens) on every single request. Prefilling 3,000 tokens at 3–5 tok/s takes approximately 600–1,000 seconds at peak. In practice, OpenVINO's parallel prefill is more efficient, but CPU-only inference for this prompt size still takes several minutes.

**Why the outputs are hallucinated:**
The `CodeActAgent` system prompt is a dense multi-thousand word document describing:
- Available tools (bash, Python, file browser)
- Output format requirements (XML block syntax for tool calls)
- Safety guidelines

A 0.5B parameter model simply does not have enough capacity to simultaneously:
1. Comprehend a multi-thousand-token instruction document
2. Follow the structured output format OpenHands expects
3. Understand and respond to the user's trailing question

The model latches onto the dominant patterns in the system prompt (scripts, bash, find commands) and ignores the actual user message. This is a known failure mode of small language models with large context inputs.

---

## API Compatibility with OpenHands

| Feature | OpenHands Requires | OVMS Provides | Status |
|---|---|---|---|
| Chat completions endpoint | `/v1/chat/completions` | `/v3/chat/completions` | ⚠️ Path differs (configurable) |
| Non-streaming response | Yes | Yes | ✅ |
| SSE streaming | Yes | Yes | ✅ |
| `choices[0].message.content` | Yes | Yes | ✅ |
| `usage.prompt_tokens` | Yes | Yes (partial) | ⚠️ |
| Tool/function calling | Heavily used | Not supported | ❌ |
| `logprobs` | Occasionally | Not supported | ❌ |
| `seed` for determinism | Sometimes | Not supported | ❌ |
| Model metadata API | Queried | Not fully implemented | ⚠️ |

---

## Key Observations About OpenHands Behaviour

1. **Settings persistence problem:** OpenHands stores LLM configuration in two places — environment variables and a `settings.json` file. If `settings.json` exists (from a previous browser session), it silently overrides environment variables. This caused hours of confusion where LLM requests appeared to be configured correctly via env vars but were actually pointing to a stale endpoint from a prior test.

2. **One sandbox per conversation:** Every new OpenHands conversation spawns a separate `openhands-runtime` Docker container (~1.5 GB RAM). If many conversations are started (for debugging), these containers accumulate and exhaust system memory. The `start_openhands.sh` script now automatically cleans these up before starting.

3. **Title creator as a diagnostic tool:** OpenHands runs a small background LLM call called `conversation_title_creator` immediately when a user sends their first message. This call is lightweight and appears in the OpenHands logs as `extraneous completion: conversation_title_creator`. Observing this log line confirms that LLM connectivity is working before the full agent prompt is processed.

4. **OVMS periodic logging:** The OVMS log line `All requests: X; Scheduled requests: Y; Cache usage Z%` is printed periodically (approximately every 6–10 minutes). This means inference in progress will not immediately appear in `docker logs` — monitoring requires either watching the periodic log or observing the KV cache growth percentage.

5. **DNS vs IP for container communication:** Using a static Docker container IP (e.g., `http://172.18.0.2:8000/v3`) in the `LLM_BASE_URL` breaks every time the OVMS container is restarted, since Docker reassigns IPs dynamically. Using the container name (`http://ovms-llm:8000/v3`) with a shared named network (`ovms-net`) is the stable, correct approach.

---

## Conclusions and Recommendations

### For this GSoC Work

The integration is architecturally proven — OVMS correctly serves requests and OpenHands correctly sends them. The end-to-end data flow is functional. The limitation is hardware-imposed, not architectural.

### For Production Usability

| Minimum Requirement | Reason |
|---|---|
| 16 GB RAM dedicated to WSL/Docker | Allows running a 7B parameter model (~8 GB) + sandbox + OS |
| A 7B+ parameter model | Required to faithfully follow OpenHands' complex system prompt |
| nvme SSD for swap | If using 7B+ models on 8 GB RAM, fast swap is critical |
| GPU (optional, significant speedup) | Would reduce response time from 3–7 minutes to ~10–30 seconds |

### Recommended Model for Demonstration

On a 16 GB+ system: **Qwen2.5-7B-Instruct** INT4 (via `optimum-cli`). It fits in ~6 GB of RAM, supports 128K context, and is capable enough to follow OpenHands' agent instructions reliably.
