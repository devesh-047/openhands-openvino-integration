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
┌──────────────────────┐     POST /v3/chat/completions      ┌────────────────────────────┐
│                      │  ──────────────────────────────►   │                            │
│   OpenHands          │                                     │  OpenVINO Model Server     │
│   (CodeActAgent UI)  │  ◄──────────────────────────────   │  (CPU Inference via        │
│                      │       JSON / SSE stream response    │   OpenVINO Runtime)        │
└──────────────────────┘                                     └────────────────────────────┘
          │                                                             │
    start_openhands.sh                                       configs/graph.pbtxt
    LLM_BASE_URL=http://ovms-llm:8000/v3                     (MediaPipe LLM pipeline)
    LLM_MODEL=openai/qwen2.5-0.5b-instruct                   docker/models/qwen2.5-0.5b-instruct/
```

Both components run as Docker containers connected to a shared `ovms-net` bridge network, allowing name-based DNS resolution between containers.

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

Verify the model is loaded:

```bash
curl -s http://localhost:8000/v1/config
```

Expected output (truncated):
```json
{
  "qwen2.5-0.5b-instruct": {
    "model_version_status": [{"state": "AVAILABLE"}]
  }
}
```

### 4. Test direct inference

Confirm inference works before involving OpenHands:

```bash
curl -s -X POST http://localhost:8000/v3/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "qwen2.5-0.5b-instruct",
      "messages": [{"role": "user", "content": "Say hello."}],
      "max_tokens": 20
  }'
```

### 5. Launch OpenHands

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

## License

Apache License 2.0
