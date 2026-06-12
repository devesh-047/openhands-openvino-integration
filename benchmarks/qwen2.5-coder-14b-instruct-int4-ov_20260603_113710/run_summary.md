# Benchmark Session

Model: `qwen2.5-coder-14b-instruct-int4-ov`

This folder captures one OpenHands benchmark run for the OVMS model currently
configured in this workspace.

## Files

- `benchmark_results.json`: per-prompt conversation results and quality scoring.
- `benchmark_summary.json`: aggregate quality, success, readiness, settle, and resource metrics.
- `benchmark_report.md`: human-readable benchmark report.
- `docker_stats.csv`: 2-second Docker CPU and memory samples for `ovms-llm` and `openhands`.
- `ovms_execution.log`: OVMS container logs for model loading and request handling.
- `openhands_execution.log`: OpenHands container logs for conversation lifecycle activity.
- `task_transcripts/`: per-prompt markdown transcripts for debugging and comparison.
- `benchmark_metadata.json`: machine-readable session metadata.

## Commands

Start telemetry and containers:

```bash
python3 scripts/start.py
```

Run the OpenHands benchmark against the active OVMS model:

```bash
python3 scripts/start.py benchmark
```

Run the benchmark with a custom prompt dataset:

```bash
python3 scripts/start.py benchmark --prompts benchmarks/prompts/default.json
```

Compare completed benchmark sessions:

```bash
python3 scripts/start.py compare
```
