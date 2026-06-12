# OpenHands + OVMS Integration

This repository contains an integration and local benchmark framework for running OpenHands through an OpenVINO Model Server (OVMS) backend. It is intended for developers and researchers who want to:

- Run the full OpenHands → OVMS workflow locally
- Benchmark the active model configured in OVMS using OpenHands conversation endpoints
- Collect telemetry (CPU/RAM/docker stats and logs) and reproducible artifacts
- Iterate on models, prompts, and deployment configurations

This project is not a packaged cloud service — it's a local developer toolkit and reproducible benchmark harness.

## Repository Layout (high level)

- `configs/` — configuration files used by the runner and OVMS (e.g. `ovms_config.json`).
- `docker/` — model artifacts and OpenVINO files for packaged models.
- `scripts/` — helper scripts (start OpenHands, deploy OVMS, benchmark runner `start.py`).
- `benchmarks/` — benchmark session output and prompt datasets.
- `models/` — local model stubs (not required to run OVMS; used for packaging).
- `ov_venv/` — optional Python virtual environment used by developers for local testing.
- `docs/` — design notes, architecture, troubleshooting and historical observations.

## Components & Responsibilities

- OpenHands (local app server): provides the conversation API used by the benchmark runner. Default base URL: `http://localhost:3000`.
- OVMS (OpenVINO Model Server): serves the model used for inference. Default base URL: `http://localhost:8000/v3`.
- `scripts/start.py`: the benchmark runner and CLI. It drives conversations through OpenHands, waits for readiness/settling, extracts event history, scores responses, and writes artifacts.
- Telemetry collectors: background processes in the runner capture `docker stats`, and container logs for reproducibility.

## Quickstart (local developer)

Prerequisites
- Linux or macOS with Docker and `docker-compose` installed
- Python 3.10+ (the repository includes an optional `ov_venv/`)
- `docker` CLI available for telemetry collection

Basic steps

1. Configure OVMS model and resources in `configs/ovms_config.json`.
2. Start OVMS (example script):

```bash
# start OVMS (implementation depends on your local setup)
bash scripts/deploy_ovms.sh
```

3. Start OpenHands in a separate terminal:

```bash
bash scripts/start_openhands.sh
```

4. Run the benchmark runner in another terminal:

```bash
python3 scripts/start.py benchmark --prompts benchmarks/prompts/default.json
```

5. Compare sessions across models:

```bash
python3 scripts/start.py compare
```

Important: the runner requires OpenHands to be reachable; it will fail early with an actionable message if the service or APIs are unavailable.

## `scripts` overview

- `scripts/start.py` — main runner and CLI. Modes: `benchmark`, `compare`, `telemetry` helpers.
- `scripts/start_openhands.sh` — helper to launch the OpenHands app server locally (keeps standard logs).
- `scripts/deploy_ovms.sh` — helper to launch OVMS (or use `docker-compose.yml` directly).
- `scripts/test_openai_endpoint.py` / `test_python.py` — small utility scripts for quick validation.

See the headers and docstrings in `scripts/start.py` for CLI flags and examples.

## Benchmarking details

The benchmark runner intentionally exercises the real OpenHands conversation flow instead of calling OVMS directly. The lifecycle is:

1. POST to OpenHands to create a conversation (a start task).
2. Poll the `start-tasks` API until the task reports `READY`.
3. Poll the conversation event history until it settles (no new events for a short window).
4. Extract the final assistant reply and score it using heuristics (quality_score 0.0–1.0).
5. Write per-prompt transcripts and session-level artifacts.

Artifacts produced per session (under `benchmarks/<model>_<timestamp>/`):

- `benchmark_results.json` — full per-prompt results and events
- `benchmark_summary.json` — aggregated metrics and reproducibility fields
- `benchmark_report.md` — human-readable session report
- `docker_stats.csv` — captured `docker stats` timeline
- `openhands_execution.log` / `ovms_execution.log` — collected container logs
- `task_transcripts/` — one markdown file per prompt run
- `benchmark_metadata.json` — reproducibility and environment details

Comparison uses weighted metrics (default weights implemented in the runner):

- 35% average quality score
- 25% success rate
- 15% average READY time
- 10% average settle time
- 10% memory peak
- 5% CPU peak

The runner validates the prompt dataset before running and fails fast with clear messages if prerequisites are missing.

## Prompt dataset

Place prompt datasets under `benchmarks/prompts/`. The default is `benchmarks/prompts/default.json`.

Each entry should be an object with at least `id`, `category`, and `prompt` fields:

```json
{
  "id": "binary_search",
  "category": "coding",
  "prompt": "Implement binary search in Python."
}
```

The runner calculates a dataset hash and stores it with each session for reproducibility.

## Telemetry and logs

Telemetry is collected during a benchmark session to help reproduce results and diagnose regressions:

- `docker_stats.csv`: periodic snapshots of container CPU/RAM from `docker stats`.
- Collected logs from the OpenHands and OVMS containers write to `openhands_execution.log` and `ovms_execution.log`.

If you want to increase telemetry fidelity, extend the collector functions in `scripts/start.py`.

## Development notes

- Code entrypoint: `scripts/start.py` — read its docstring for CLI flags.
- Use the repository `ov_venv` or create a fresh virtualenv and install `requirements.txt` for development.
- The runner's scoring heuristics live in `scripts/start.py` (search for `score_prompt_quality`). Tweak thresholds and weights there.

## Troubleshooting

- OpenHands unreachable: ensure `scripts/start_openhands.sh` completed and the server listens on `http://localhost:3000`.
- OVMS unreachable: check `scripts/deploy_ovms.sh` or `docker-compose.yml` and verify the model is loaded.
- Docker telemetry missing: `docker` CLI must be installed and the user must have permission to run `docker stats`.
- Permission errors writing `benchmarks/`: ensure the current user can create directories under the project.

If a benchmark fails, the runner writes failure artifacts to the session folder and prints actionable messages.

## Where to look next

- Benchmark runner: [scripts/start.py](scripts/start.py)
- OpenHands helper: [scripts/start_openhands.sh](scripts/start_openhands.sh)
- OVMS configuration: [configs/ovms_config.json](configs/ovms_config.json)
- Prompt datasets: [benchmarks/prompts](benchmarks/prompts)
- Docs and architecture notes: [docs/](docs)

---

If you'd like, I can also add a short quickstart example that runs a full end-to-end benchmark locally and commits a sample session to `benchmarks/` for documentation. Would you like that next?
