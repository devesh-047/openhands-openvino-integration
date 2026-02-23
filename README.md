# OpenHands + OpenVINO Model Server Integration

Integration of [OpenHands](https://github.com/All-Hands-AI/OpenHands) with [OpenVINO Model Server](https://github.com/openvinotoolkit/model_server) (OVMS) for local, self-hosted LLM inference powering AI coding agents.

## Overview

OpenHands is a GUI-based platform for AI coding agents that communicates with LLM backends through the OpenAI Chat Completions API. OpenVINO Model Server can serve LLMs with an OpenAI-compatible REST interface, enabling fully local inference without external API dependencies.

This repository provides:

- Deployment configuration for OVMS with LLM serving enabled.
- Validation scripts to verify API compatibility between OVMS and OpenHands.
- Documentation of integration behavior, limitations, and configuration.
- Usability and gap analysis comparing OVMS against the OpenAI API specification.

## Architecture

```
+-------------------+     POST /v3/chat/completions     +-------------------------+
|                   | --------------------------------> |                         |
|    OpenHands      |                                   |   OpenVINO Model Server |
|    (Agent UI)     | <-------------------------------- |   (CPU Inference)       |
|                   |         JSON / SSE response       |                         |
+-------------------+                                   +-------------------------+
        |                                                         |
   config.toml                                           OpenVINO IR model
   LLM_BASE_URL = http://localhost:8000/v3               (INT8 quantized)
```

Both components run as Docker containers on the same host. OpenHands sends standard chat completion requests to OVMS, which performs inference using the OpenVINO runtime and returns responses in OpenAI-compatible format.

See [docs/architecture.md](docs/architecture.md) for the full architectural description.

## Repository Structure

```
.
├── README.md
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── ovms_config.json          # OVMS model registration
│   ├── graph.pbtxt               # MediaPipe LLM serving graph
│   └── env.template              # Environment variable template
├── scripts/
│   ├── deploy_ovms.sh            # OVMS container deployment
│   ├── test_openai_endpoint.py   # Endpoint validation with schema checks
│   ├── validate_chat_completion.py # Multi-turn conversation test
│   ├── run_stream_test.py        # SSE streaming validation
│   ├── collect_usability_metrics.py # Latency and throughput measurement
│   └── _config.py                # Shared configuration loader
├── docs/
│   ├── architecture.md           # System design and component topology
│   ├── openai_compatibility_notes.md # API feature support matrix
│   ├── openhands_configuration.md    # OpenHands setup instructions
│   └── known_limitations.md      # Documented constraints and workarounds
├── analysis/
│   ├── usability_report.md       # Integration usability evaluation
│   └── gaps_analysis.md          # API gap analysis with recommendations
└── docker/
    └── models/                   # Model directory (not checked in)
```

## Prerequisites

- Ubuntu 20.04+ (native or WSL2)
- Docker Engine 24+ or Docker Desktop 4+
- Python 3.10+
- 8 GB RAM minimum (16 GB recommended)
- 5 GB disk for model files and container images

## Environment Setup

Clone the repository and create the configuration:

```bash
git clone https://github.com/devesh-047/openhands-openvino-integration.git
cd openhands-openvino-integration

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp configs/env.template configs/.env
```

Edit `configs/.env` if you need to change default ports or model paths.

---

## Phase 1: Deploy OpenVINO Model Server with LLM

### Objective

Serve a quantized LLM through OVMS using the MediaPipe LLM pipeline, exposing an OpenAI-compatible REST API on the host.

### Technical Context

OVMS uses a MediaPipe graph (`graph.pbtxt`) to route HTTP requests through the LLM inference pipeline. The model must be in OpenVINO IR format. We use `optimum-intel` to convert a Hugging Face model with INT8 weight quantization for CPU-efficient inference.

### Steps

#### 1.1 Convert the model

Install the conversion tools:

```bash
pip install optimum[openvino] openvino-tokenizers
```

Export TinyLlama 1.1B Chat to OpenVINO IR with INT8 quantization:

```bash
optimum-cli export openvino \
    --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
    --weight-format int8 \
    docker/models/tiny-llama-1.1b-chat
```

This produces IR files (`openvino_model.xml`, `openvino_model.bin`, `tokenizer.xml`, etc.) in the target directory.

#### 1.2 Verify model directory structure

```bash
ls docker/models/tiny-llama-1.1b-chat/
```

Expected contents include the OpenVINO IR files plus the `graph.pbtxt` pipeline definition:

```
graph.pbtxt
openvino_model.xml
openvino_model.bin
openvino_tokenizer.xml
openvino_tokenizer.bin
openvino_detokenizer.xml
openvino_detokenizer.bin
tokenizer_config.json
special_tokens_map.json
```

The `graph.pbtxt` file defines the MediaPipe LLM serving pipeline for OVMS. It is already present in this repository at `docker/models/tiny-llama-1.1b-chat/graph.pbtxt`.

#### 1.3 Deploy OVMS

```bash
chmod +x scripts/deploy_ovms.sh
./scripts/deploy_ovms.sh
```

The script:
- Removes any existing OVMS container.
- Starts a new container with model and config volumes mounted.
- Polls the health endpoint until OVMS reports ready.

#### 1.4 Verify deployment

Check that the model has loaded and reports AVAILABLE status:

```bash
curl -s http://localhost:8000/v1/config
```

Expected output:

```json
{
    "tiny-llama-1.1b-chat": {
        "model_version_status": [
            {
                "version": "1",
                "state": "AVAILABLE",
                "status": {
                    "error_code": "OK",
                    "error_message": "OK"
                }
            }
        ]
    }
}
```

Note: `curl http://localhost:8000/v2/health/ready` also succeeds (HTTP 200) but returns an empty body and reflects only HTTP server readiness, not model readiness. Use `/v1/config` to confirm the LLM pipeline has fully initialized.

### Troubleshooting

| Symptom                          | Cause                                         | Resolution                                      |
|----------------------------------|-----------------------------------------------|--------------------------------------------------|
| Container exits immediately      | Missing or malformed model files               | Re-run model conversion; check `docker logs ovms-llm` |
| Health endpoint returns 503      | Model still loading                            | Wait 30-60 seconds; large models take longer     |
| Port conflict                    | Another service on port 8000                   | Change `OVMS_REST_PORT` in `configs/.env`        |
| Permission denied on model files | Docker cannot read mounted volume              | Check file ownership and permissions             |

---

## Phase 2: Validate OpenAI-Compatible API

### Objective

Confirm that OVMS responds correctly to OpenAI Chat Completions API requests with proper schema conformance and streaming support.

### Technical Context

OVMS exposes `/v3/chat/completions`. The validation scripts send requests with varying parameters and validate response structure using Pydantic models. Streaming validation parses SSE chunks and measures time to first token.

### Steps

#### 2.1 Test basic endpoint

```bash
cd scripts
python test_openai_endpoint.py
```

This sends a single chat completion request and validates the response against the expected schema (id, object, choices, usage fields).

Expected log output:

```
INFO: Sending request to http://localhost:8000/v3/chat/completions
INFO: Response validated successfully.
INFO: Model: tiny-llama-1.1b-chat
INFO: Finish reason: stop
INFO: Content: The capital of France is Paris...
```

#### 2.2 Test multi-turn conversation

```bash
python validate_chat_completion.py
```

Sends a two-turn conversation to verify that OVMS correctly processes message history.

#### 2.3 Test streaming

```bash
python run_stream_test.py
```

Sends a streaming request and reports chunk count, time to first token, and assembled content.

Expected log output:

```
INFO: Time to first token: 1.234 seconds
INFO: Received 15 chunks in 4.567 seconds
INFO: Streaming validation passed.
```

#### 2.4 Manual curl verification

Non-streaming:

```bash
curl -s http://localhost:8000/v3/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "tiny-llama-1.1b-chat",
        "messages": [{"role": "user", "content": "What is OpenVINO?"}],
        "max_tokens": 64,
        "temperature": 0.1
    }' | python3 -m json.tool
```

Streaming:

```bash
curl -N http://localhost:8000/v3/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "tiny-llama-1.1b-chat",
        "messages": [{"role": "user", "content": "Count to 5."}],
        "max_tokens": 32,
        "stream": true
    }'
```

### Troubleshooting

| Symptom                    | Cause                                  | Resolution                                    |
|----------------------------|----------------------------------------|------------------------------------------------|
| Connection refused         | OVMS not running or wrong port         | `docker ps`; check `OVMS_REST_PORT`           |
| 404 on /v3/chat/completions| OVMS version does not support LLM serving | Use OVMS 2024.1 or later                    |
| Model not found error      | Model name mismatch                    | Verify name in `ovms_config.json`             |
| Schema validation failure  | OVMS response format changed           | Check OVMS release notes; update Pydantic models |

---

## Phase 3: Configure and Integrate OpenHands

### Objective

Connect OpenHands to the OVMS endpoint so that the AI coding agent uses local inference for all LLM interactions.

### Technical Context

OpenHands accepts LLM backend configuration through environment variables or `config.toml`. The critical parameters are `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`. Since both applications run in Docker, container networking must be configured correctly.

### Steps

#### 3.1 Launch OpenHands

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

On Linux without Docker Desktop, replace `host.docker.internal` with the Docker bridge gateway (typically `172.17.0.1`):

```bash
docker run -d \
    --name openhands \
    --add-host=host.docker.internal:host-gateway \
    -p 3000:3000 \
    -e LLM_BASE_URL="http://host.docker.internal:8000/v3" \
    -e LLM_MODEL="tiny-llama-1.1b-chat" \
    -e LLM_API_KEY="unused" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    ghcr.io/all-hands-ai/openhands:latest
```

#### 3.2 Verify the web UI

Open `http://localhost:3000` in a browser. Navigate to settings and confirm the LLM configuration shows the OVMS model.

#### 3.3 Test agent interaction

1. Create a new workspace in OpenHands.
2. Send a test prompt: "Create a Python function that checks if a number is prime."
3. Observe that the agent receives a response from OVMS and produces output.
4. Check OVMS logs for the incoming request: `docker logs ovms-llm --tail 20`

#### 3.4 Verify chat completion through the agent

Monitor the OpenHands container logs to confirm requests are being sent to the configured endpoint:

```bash
docker logs openhands --tail 50 | grep -i "llm\|model\|base_url"
```

### Troubleshooting

| Symptom                               | Cause                                      | Resolution                                         |
|---------------------------------------|--------------------------------------------|----------------------------------------------------|
| OpenHands shows "LLM error"           | Cannot reach OVMS from container           | Use `host.docker.internal` or bridge IP            |
| Responses are empty or incoherent     | Model too small for complex tasks          | Expected for TinyLlama; upgrade model for real use |
| Agent hangs                           | Long inference time on CPU                 | Wait; check `docker stats` for resource usage      |
| "API key required" error              | `LLM_API_KEY` not set                      | Set to any non-empty string                        |

See [docs/openhands_configuration.md](docs/openhands_configuration.md) for detailed configuration reference.

---

## Running the Usability Analysis

Collect latency and throughput metrics:

```bash
cd scripts
python collect_usability_metrics.py
```

Results are written to `analysis/metrics_report.json`. See [analysis/usability_report.md](analysis/usability_report.md) for interpretation and [analysis/gaps_analysis.md](analysis/gaps_analysis.md) for the API gap analysis.

## Documentation Index

| Document                                                              | Description                                  |
|-----------------------------------------------------------------------|----------------------------------------------|
| [docs/architecture.md](docs/architecture.md)                          | System design and deployment topology        |
| [docs/openai_compatibility_notes.md](docs/openai_compatibility_notes.md) | API feature support matrix                |
| [docs/openhands_configuration.md](docs/openhands_configuration.md)    | OpenHands setup and networking               |
| [docs/known_limitations.md](docs/known_limitations.md)                | Constraints, workarounds, and caveats        |
| [analysis/usability_report.md](analysis/usability_report.md)          | Integration usability evaluation             |
| [analysis/gaps_analysis.md](analysis/gaps_analysis.md)                | API gap analysis with recommendations        |

## License

Apache License 2.0
