# Troubleshooting History

This document is a chronological record of every significant problem encountered while setting up and running the OpenHands + OpenVINO Model Server integration on WSL2. Each entry includes the exact symptom, the root cause, and the resolution.

---

## 1. Python Compatibility: `jiter` Package Error

**Symptom:**
`pip install -r requirements.txt` failed with `No matching distribution found for jiter>=0.10.0`.

**Root Cause:**
Ubuntu 20.04 ships Python 3.8 by default. Modern ML libraries (`openai`, `pydantic`, `optimum`) have dropped Python 3.8 support and require 3.10+.

**Resolution:**
Installed Python 3.12 via the `deadsnakes` PPA, recreated the virtual environment (`python3.12 -m venv .venv`), and reinstalled all packages. All dependency conflicts resolved.

---

## 2. Docker Not Found in WSL2

**Symptom:**
Running `docker ps` in the Ubuntu terminal returned `The command 'docker' could not be found in this WSL 2 distro.`

**Root Cause:**
Docker Desktop requires manual opt-in to expose the Docker CLI into each WSL distribution.

**Resolution:**
In Docker Desktop → Settings → Resources → WSL Integration, toggled on `Ubuntu-20.04`. Added the current user to the docker group with `sudo usermod -aG docker $USER` and reloaded with `newgrp docker`.

---

## 3. Missing `graph.pbtxt` in Model Directory

**Symptom:**
`deploy_ovms.sh` exited with `ERROR: graph.pbtxt not found in ./docker/models/`.

**Root Cause:**
`optimum-cli export openvino` only outputs the OpenVINO IR model files (`.xml`, `.bin`, tokenizer). It does not generate the `graph.pbtxt` MediaPipe pipeline definition that OVMS requires to route HTTP requests through the LLM inference graph.

**Resolution:**
Manually copied the pre-written `configs/graph.pbtxt` template into the model directory:
```bash
cp configs/graph.pbtxt docker/models/<model-dir>/
```

---

## 4. OVMS `FAILED_PRECONDITION`: Missing LLM Libraries

**Symptom:**
`docker logs ovms-llm` showed `Could not find type type.googleapis.com/mediapipe.LlmCalculatorOptions`, and the model state was `LOADING_PRECONDITION_FAILED`.

**Root Cause:**
The standard `openvino/model_server:latest` image on Docker Hub strips all LLM-specific C++ shared libraries to reduce image size. Standard images only support computer vision inference.

**Resolution:**
Switched to the GPU-enabled image tag (`openvino/model_server:latest-gpu` → eventually `openvino/model_server:latest`). The GPU variant bundles all OpenVINO LLM extensions, including the `HttpLLMCalculator` C++ node. This lets CPU-only inference work with the LLM pipeline without needing a physical GPU.

---

## 5. `HttpLLMCalculator` Not Found (Graph Naming Change)

**Symptom:**
After switching to the correct image, OVMS still failed with `No registered object with name: LLMCalculator`.

**Root Cause:**
The graph node name changed between OpenVINO 2024.x and 2026.x releases. The `graph.pbtxt` template referenced the old `LLMCalculator` identifier, which no longer exists in the compiled 2026 server binary.

**Resolution:**
Updated `configs/graph.pbtxt` to reference the new node name `HttpLLMCalculator`. This also changed the serving URL from `/v1/chat/completions` to `/v3/chat/completions`.

---

## 6. `ovms_config.json` Incorrect Schema

**Symptom:**
OVMS started but immediately shut down. Logs read:  
`JSON schema parse error:#/properties/mediapipe_config_list. Keyword:type`  
Later: `JSON schema parse error:#/definitions/mediapipe_config. Keyword:additionalProperties`

**Root Cause:**
Our automated edit to `ovms_config.json` when switching models introduced two sequential JSON formatting errors:
1. First: removed the array brackets `[ ]` around the config list, making it an object instead of an array.
2. After fixing that: accidentally wrapped `name` and `base_path` inside an extra `{ "config": {} }` layer. The OVMS JSON schema expects these keys directly inside the array element.

**Correct format:**
```json
{
  "model_config_list": [],
  "mediapipe_config_list": [
    {
      "name": "qwen2.5-0.5b-instruct",
      "base_path": "/models/qwen2.5-0.5b-instruct"
    }
  ]
}
```

---

## 7. `cache_size: 0.5` Protobuf Parse Error

**Symptom:**
`docker logs ovms-llm` showed:  
`Error parsing text-format mediapipe.CalculatorGraphConfig: 19:23: Expected integer, got: 0.5`  
Model state: `LOADING_PRECONDITION_FAILED`

**Root Cause:**
The `cache_size` field in `graph.pbtxt` is defined as a `uint32` integer type in the protobuf schema. Protobuf's text format parser does not accept floating-point values for integer fields. We had set `cache_size: 0.5` trying to reduce KV cache memory allocation.

**Resolution:**
Changed `cache_size` back to `1` (integer). With the Qwen2.5-0.5B model, the 1GB KV cache pre-allocation is within the memory budget since the model itself only uses ~0.8GB.

---

## 8. OVMS OOM Crash — Exit Code 137

**Symptom:**
`docker inspect ovms-llm` showed `"ExitCode": 137`. OVMS container was repeatedly crashing silently.

**Root Cause:**
Exit code 137 means `SIGKILL` — the Linux OOM (Out of Memory) killer killed the process. The Phi-3.5-mini-instruct model at 3.8B parameters required approximately 5.3 GB RAM for model weights, KV cache, and OpenVINO compute buffers. When the OpenHands runtime sandbox container (a separate Docker container) started alongside it, total WSL2 memory was exhausted.

**Contributing factors:**
- Multiple orphaned `openhands-runtime` containers from prior conversations were still running and consuming ~1.6 GB each
- WSL2 `.wslconfig` had `memory=1.3GB` (default), far below requirements

**Resolution:**
1. Increased WSL2 memory to 8 GB: added `memory=8GB` and `swap=8GB` to `~/.wslconfig` in Windows, then ran `wsl --shutdown`
2. Added automatic cleanup of orphaned runtime containers in `start_openhands.sh`
3. Added `SANDBOX_DOCKER_ARGS=--memory=1536m --memory-swap=1536m` to cap each runtime container at 1.5 GB

---

## 9. OVMS Hang with `cache_size: 0`

**Symptom:**
Setting `cache_size: 0` to minimize memory worked for startup, but all inference requests hung indefinitely. The Python test timed out after 120 seconds with no response.

**Root Cause:**
Setting `cache_size: 0` prevents OVMS from pre-allocating the KV (Key-Value) cache buffer. Without a non-zero cache, the autoregressive token generation stage cannot store intermediate attention states, causing the inference scheduler to stall permanently.

**Resolution:**
Must use a non-zero integer. With Phi-3.5-mini: `cache_size: 1` caused OOM alongside the runtime. With Qwen2.5-0.5B: `cache_size: 1` fits comfortably since the model is only ~0.8 GB.

---

## 10. `CANCELLED: CalculatorGraph::Run()` Errors During Inference

**Symptom:**
OVMS logs showed repeated `CANCELLED: CalculatorGraph::Run() failed: Calculator::Process() for node "LLMExecutor" failed`. Requests showed 0 scheduled even when OpenHands sent them.

**Root Cause:**
This is the MediaPipe graph's way of propagating an OOM kill during inference. When the Linux OOM killer sends `SIGKILL` to a thread inside the inference engine, the MediaPipe scheduler marks all pending graph runs as `CANCELLED`. This was consistently caused by the Phi-3.5-mini model using so much RAM that there was insufficient heap available for the actual forward pass computation.

**Resolution:**
Switched model entirely to Qwen2.5-0.5B-Instruct. With a much smaller model footprint, this error never occurred.

---

## 11. OpenHands LLM Requests Not Reaching OVMS

**Symptom:**
OpenHands logs showed the agent going to `RUNNING` state after the user sent a message, but OVMS logs continued to show `Scheduled requests: 0`. No LiteLLM errors were logged.

**Root Cause (Investigated extensively):**
Multiple factors were investigated and ruled out one by one:
- ❌ DNS resolution: `curl` from inside the OpenHands container successfully reached OVMS
- ❌ Port binding: OVMS was listening on `0.0.0.0:8000`
- ❌ Model name: tested with both `phi-3.5-mini-instruct` and the `openai/` prefix
- ✅ **Root cause**: OpenHands persisted old settings (`oh_settings.json`) that overrode environment variables, pointing to a stale OVMS IP from a prior session. Also, the static IP of the OVMS container changed across restarts.

**Resolution:**
- `start_openhands.sh` now writes the correct current OVMS container IP to `.openhands/settings.json` *before* starting the container, mounting it as a volume
- Updated `LLM_BASE_URL` to use the stable Docker DNS name `http://ovms-llm:8000/v3` instead of the dynamic IP
- Added `--network ovms-net` to the OpenHands `docker run` command so it can resolve `ovms-llm` by name

---

## 12. TinyLlama Context Window Exceeded

**Symptom:**
OpenHands appeared to send requests, but responses were empty or immediately truncated. OVMS showed errors related to context length.

**Root Cause:**
TinyLlama-1.1B has a maximum context window of 2,048 tokens. OpenHands' `CodeActAgent` system prompt alone is approximately 2,000+ tokens. Any user message pushed the total over the limit, causing the tokenizer to truncate or reject the request entirely.

**Resolution:**
Switched to Qwen2.5-0.5B-Instruct, which supports a 32,768-token context window — sufficient for OpenHands' full system prompt plus several turns of conversation.

---

## 13. C: Drive Disk Space Exhausted During Docker Image Pulls

**Symptom:**
`docker pull openvino/model_server:latest-gpu` failed with `no space left on device`.

**Root Cause:**
Docker Desktop stores its WSL virtual disk (`ext4.vhdx`) on the C: drive by default. Large images (the GPU-enabled OVMS image is ~4 GB) combined with build cache and dangling intermediate layers rapidly filled the available space.

**Resolution:**
Ran `docker builder prune -a -f` and `docker image prune -a -f` to reclaim space from unused layers. Optionally, Docker data can be relocated to the D: drive — instructions are in `docker_storage_migration.md`.
