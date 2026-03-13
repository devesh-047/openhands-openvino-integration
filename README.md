# OpenHands + OpenVINO Model Server Integration

A working integration of [OpenHands](https://github.com/All-Hands-AI/OpenHands) with [OpenVINO Model Server](https://github.com/openvinotoolkit/model_server) (OVMS) for fully local, self-hosted LLM inference powering AI coding agents — with no external API dependency.

> **GSoC 2026 Proposal:** This repository serves as a proof-of-concept and technical investigation for the proposed GSoC project: *"Enabling local LLM backends for OpenHands via OpenVINO Model Server"*.

---

## Project Goal

OpenHands communicates with LLM providers through an OpenAI-compatible REST API. This project validates whether OpenVINO Model Server can act as a drop-in local backend, enabling OpenHands to run entirely offline on consumer hardware using CPU-based inference.

The repository covers the full lifecycle of this integration:
- Deploying OVMS with a quantized LLM
- Exposing an OpenAI-compatible endpoint
- Connecting OpenHands to that endpoint
- Documenting API compatibility gaps and hardware constraints

---

## Architecture

```
OpenHands (CodeActAgent UI)
    │
    │  POST /v3/chat/completions
    ▼
OpenVINO Model Server  (CPU inference via OpenVINO Runtime)
    │
    ├── configs/graph.pbtxt     (MediaPipe LLM pipeline)
    └── docker/models/qwen2.5-0.5b-instruct/
```

Both components run as Docker containers on a shared `ovms-net` bridge network. Full topology in [`docs/architecture.md`](docs/architecture.md).

---

## Quick Demo

After completing setup (see below), verify the system in three steps:

**1. Start OVMS**
```bash
bash scripts/deploy_ovms.sh
```

**2. Verify model is loaded**
```bash
curl -s http://localhost:8000/v1/config
```
Expected output:
```json
{"qwen2.5-0.5b-instruct": {"model_version_status": [{"state": "AVAILABLE"}]}}
```

**3. Run a test inference**
```bash
curl -s -X POST http://localhost:8000/v3/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5-0.5b-instruct", "messages": [{"role": "user", "content": "Say hello."}], "max_tokens": 20}'
```
You should receive a JSON response with `choices[0].message.content` containing generated text within ~10 seconds on CPU.

---

## Key Findings

### What Works
- OVMS correctly implements the `/v3/chat/completions` endpoint with OpenAI-compatible schema
- Non-streaming and streaming (SSE) requests both function correctly
- OpenHands successfully connects, issues requests, and renders responses
- The full inference pipeline runs on CPU with no GPU requirement

### Known Limitations
- **API gaps:** `functions`/`tools` (function calling), `logprobs`, `seed`, and `response_format` are not supported by OVMS
- **Memory ceiling:** On 8 GB WSL2, only models up to ~0.5B parameters can run alongside the OpenHands sandbox
- **Response quality:** 0.5B parameter models cannot reliably follow the complex multi-instruction system prompt OpenHands sends per request — outputs are often hallucinated content

Full details: [`docs/known_limitations.md`](docs/known_limitations.md) | [`docs/observations.md`](docs/observations.md)

---

## Model Benchmark Summary

All three models were tested end-to-end with OpenHands on 8 GB WSL2 (CPU-only).

| Model | Parameters | Context | Peak RAM | Result |
|-------|-----------|---------|----------|--------|
| TinyLlama-1.1B-Chat | 1.1B | 2K tokens | ~1 GB | ❌ Context too small for OpenHands system prompt |
| Phi-3.5-mini-instruct | 3.8B | 128K tokens | ~6.3 GB | ❌ OOM crash on every inference request |
| Qwen2.5-0.5B-Instruct | 0.5B | 32K tokens | ~1.5–2 GB | ✅ Loads and infers; outputs hallucinated on complex prompts |

> **Recommended for 16 GB+ systems:** Qwen2.5-7B-Instruct INT4 (~6 GB RAM, 128K context) — capable enough to follow OpenHands' agent instructions reliably.

Full per-model analysis, memory breakdowns, and performance metrics: [`docs/observations.md`](docs/observations.md)

---

## API Compatibility Summary

OVMS implements a subset of the OpenAI Chat Completions API. Key differences:

| Feature | Status | Notes |
|---------|--------|-------|
| Chat completions endpoint | ✅ | Uses `/v3` prefix instead of `/v1` |
| Non-streaming response | ✅ | Full OpenAI schema |
| SSE streaming | ✅ | `usage` may be absent from stream |
| `functions` / `tools` | ❌ | Not implemented |
| `response_format` (JSON mode) | ❌ | Not implemented |
| `logprobs` / `seed` | ❌ | Not implemented |
| Error response format | ⚠️ | Flat string, not nested OpenAI object |

Full feature matrix with impact analysis: [`docs/openai_compatibility_notes.md`](docs/openai_compatibility_notes.md) | [`analysis/gaps_analysis.md`](analysis/gaps_analysis.md)

---

## Repository Structure

```
.
├── README.md
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── ovms_config.json          # OVMS model registration (MediaPipe pipeline)
│   ├── graph.pbtxt               # MediaPipe graph definition for LLM serving
│   └── .env                      # Environment variables (image tag, model path)
├── scripts/
│   ├── deploy_ovms.sh            # Deploy and health-check the OVMS container
│   ├── start_openhands.sh        # Configure and launch OpenHands container
│   ├── test_openai_endpoint.py   # Validate /v3/chat/completions schema
│   ├── validate_chat_completion.py  # Multi-turn conversation test
│   ├── run_stream_test.py        # SSE streaming validation
│   └── collect_usability_metrics.py # Latency and throughput measurement
├── docs/
│   ├── architecture.md
│   ├── openhands_configuration.md
│   ├── openai_compatibility_notes.md
│   ├── known_limitations.md
│   ├── troubleshooting_history.md  # Full record of every issue encountered and resolved
│   └── observations.md             # LLM comparison, performance results, and findings
└── docker/
    └── models/                   # Model files (not checked in — see setup below)
```

---

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| OS | Ubuntu 20.04+ or WSL2 | Tested on WSL2 (Windows 11) |
| Docker | Engine 24+ | GPU image used for LLM support |
| Python | 3.10+ | 3.12 recommended for latest libs |
| RAM | 8 GB | 16 GB recommended for larger models |
| Disk | 10 GB free | Model + Docker images |

---

## Setup

### 1. Clone and configure environment

```bash
git clone https://github.com/devesh-047/openhands-openvino-integration.git
cd openhands-openvino-integration

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Download and convert the model

Install the OpenVINO export tool:

```bash
pip install optimum[openvino]
```

Download and convert Qwen2.5-0.5B-Instruct to OpenVINO INT4 format (~500MB output):

```bash
optimum-cli export openvino \
    --model Qwen/Qwen2.5-0.5B-Instruct \
    --weight-format int4 \
    docker/models/qwen2.5-0.5b-instruct

cp configs/graph.pbtxt docker/models/qwen2.5-0.5b-instruct/
```

> **Why this model?** It is the smallest available model with a 32,768-token context window, which is required to fit OpenHands' multi-thousand-token system prompts without truncation. See [`docs/observations.md`](docs/observations.md) for a full model comparison.

### 3. Deploy OpenVINO Model Server

```bash
chmod +x scripts/deploy_ovms.sh
bash scripts/deploy_ovms.sh
```

The script creates a Docker network (`ovms-net`), starts the container with the model mounted, and polls the `/v1/config` endpoint until the model reports `AVAILABLE`. This takes 30–90 seconds.

### 4. Launch OpenHands

```bash
bash scripts/start_openhands.sh
```

The script:
- Finds the OVMS container IP and sets `LLM_BASE_URL` to use the `ovms-llm` DNS name
- Writes the correct settings to `.openhands/settings.json` (prevents browser-cached overrides)
- Cleans up orphaned runtime sandbox containers from earlier sessions
- Starts the OpenHands container connected to `ovms-net`
- Caps each agent sandbox at 1.5 GB to protect OVMS memory

Open `http://localhost:3000` in your browser.

---

## Validation Scripts

All scripts live in `scripts/`. Run them after OVMS is deployed:

```bash
# Test schema conformance of the chat completions response
python scripts/test_openai_endpoint.py

# Test multi-turn conversation handling
python scripts/validate_chat_completion.py

# Test SSE streaming output
python scripts/run_stream_test.py

# Collect latency and throughput metrics
python scripts/collect_usability_metrics.py
```

---

## Documentation

| Document | Description |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System topology and component interaction |
| [`docs/openhands_configuration.md`](docs/openhands_configuration.md) | OpenHands networking and environment config |
| [`docs/openai_compatibility_notes.md`](docs/openai_compatibility_notes.md) | API feature support matrix |
| [`docs/known_limitations.md`](docs/known_limitations.md) | Constraints, workarounds, and open issues |
| [`docs/troubleshooting_history.md`](docs/troubleshooting_history.md) | Every issue encountered and how it was resolved |
| [`docs/observations.md`](docs/observations.md) | LLM comparison, performance metrics, final outputs |
| [`analysis/usability_report.md`](analysis/usability_report.md) | Integration usability evaluation |
| [`analysis/gaps_analysis.md`](analysis/gaps_analysis.md) | API gap analysis with recommendations |

---

## Future Work

- **Hardware-aware model selection:** A tool or guide recommending models based on available RAM (e.g., 8 GB → 0.5B INT4, 16 GB → 7B INT4, 32 GB → 13B INT4)
- **Broader model benchmarking:** Test Qwen2.5-7B, Mistral-7B, and Phi-3-mini on systems with ≥16 GB RAM
- **Prompt compression:** Investigate system prompt summarization or token reduction techniques to make small models more viable
- **Response normalization proxy:** Lightweight middleware to inject missing OpenAI envelope fields (`id`, `object`, `created`) and normalize error schemas
- **GPU inference evaluation:** Measure latency improvement on consumer GPUs to quantify the CPU→GPU speedup for OVMS LLM serving

---

## License

Apache License 2.0
