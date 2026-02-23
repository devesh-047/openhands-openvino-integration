# Architecture

## System Overview

This project integrates two independent systems through an HTTP API boundary:

1. **OpenVINO Model Server (OVMS)** -- serves an LLM using OpenVINO runtime optimizations, exposing an OpenAI-compatible REST API.
2. **OpenHands** -- a local GUI for AI coding agents that communicates with any OpenAI-compatible backend.

The integration is stateless from the server perspective. OpenHands sends standard `/v3/chat/completions` requests; OVMS processes inference and returns responses in the same schema.

## Component Diagram

```
+-------------------+        HTTP (REST)        +-------------------------+
|                   |  POST /v3/chat/completions |                         |
|    OpenHands      | ------------------------> |   OpenVINO Model Server |
|    (Agent UI)     | <------------------------ |   (OVMS + MediaPipe)    |
|                   |     JSON / SSE response    |                         |
+-------------------+                            +-------------------------+
        |                                                  |
        |  Configured via:                                 |  Loads:
        |  - LLM_BASE_URL                                  |  - OpenVINO IR model
        |  - LLM_MODEL                                     |  - graph.pbtxt
        |  - LLM_API_KEY (unused)                          |  - ovms_config.json
        |                                                  |
        v                                                  v
   config.toml                                    /models/<model-name>/
```

## Request Flow

1. User interacts with OpenHands web UI.
2. OpenHands agent formulates a prompt with system instructions and conversation history.
3. Agent sends a `POST /v3/chat/completions` request to the configured `LLM_BASE_URL`.
4. OVMS receives the request, routes it through the MediaPipe LLM graph.
5. The graph invokes the OpenVINO inference engine on the loaded IR model.
6. OVMS returns the response as either a single JSON payload or a stream of SSE chunks.
7. OpenHands parses the response and continues the agent loop.

## Deployment Topology

In the default configuration, both OVMS and OpenHands run on the same host:

```
Host Machine (WSL Ubuntu)
|
|-- Docker Container: OVMS
|     Port 8000 (REST), Port 9000 (gRPC)
|     Volumes: model directory, config files
|
|-- Docker Container: OpenHands
|     Port 3000 (Web UI)
|     Environment: LLM_BASE_URL=http://host.docker.internal:8000/v3
```

For cross-container networking, OpenHands must reach OVMS via `host.docker.internal` or the Docker bridge network IP, not `localhost`.

## Model Serving Pipeline

OVMS uses a MediaPipe graph to serve LLMs. The graph definition (`graph.pbtxt`) specifies:

- Input: HTTP request payload containing the chat completion request.
- Processing: `HttpLLMCalculator` node handles tokenization, inference scheduling, and response formatting.
- Output: HTTP response payload in OpenAI-compatible format.

The model itself must be in OpenVINO IR format. Conversion from Hugging Face format is done using `optimum-intel`:

```
optimum-cli export openvino \
    --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
    --weight-format int8 \
    docker/models/tiny-llama-1.1b-chat
```

## Key Design Decisions

- **CPU-only deployment**: The default configuration targets CPU inference to maximize accessibility. No GPU drivers or specialized hardware are required.
- **INT8 quantization**: Reduces memory footprint and improves CPU inference throughput at acceptable quality for development and testing.
- **TinyLlama 1.1B**: Selected as the reference model for its small size (~600MB in INT8), fast inference, and compatibility with the chat template expected by OpenHands.
- **Stateless API**: OVMS does not maintain conversation state. Context management is handled entirely by the client (OpenHands).
