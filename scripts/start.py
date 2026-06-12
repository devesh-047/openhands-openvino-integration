#!/usr/bin/env python3
"""
Unified project entrypoint for OVMS startup telemetry and OpenHands benchmarks.

By default this runs the non-invasive Docker Compose telemetry wrapper. Use the
`benchmark` command to drive the real OpenHands V1 conversation lifecycle
against the OVMS model currently configured in this workspace, or `compare` to
rank completed benchmark sessions.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "ovms_config.json"
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
PROMPTS_DIR = BENCHMARKS_DIR / "prompts"
DEFAULT_PROMPTS_FILE = PROMPTS_DIR / "default.json"
TESTED_MODELS_LOG = BENCHMARKS_DIR / "tested_models.log"
LATEST_SESSION_FILE = BENCHMARKS_DIR / "latest_session.json"
BENCHMARK_RESULTS_FILE = "benchmark_results.json"
BENCHMARK_SUMMARY_FILE = "benchmark_summary.json"
BENCHMARK_REPORT_FILE = "benchmark_report.md"
COMPARISON_REPORT_FILE = BENCHMARKS_DIR / "comparison_report.md"
COMPOSE_COMMAND = ["docker", "compose", "up"]
OVMS_CONTAINER = os.environ.get("OVMS_CONTAINER", "ovms-llm")
OPENHANDS_CONTAINER = os.environ.get("OPENHANDS_CONTAINER", "openhands")
TARGET_CONTAINERS = {OVMS_CONTAINER, OPENHANDS_CONTAINER}
STATS_FORMAT = "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}"
OVMS_URL = "http://localhost:8000/v3"
OPENHANDS_URL = os.environ.get("OPENHANDS_BASE_URL", "http://localhost:3000")
BENCHMARK_WORKFLOW = "openhands-v1-conversation-benchmark"
BENCHMARK_VERSION = "1.0"
BENCHMARK_CONVERSATION_TIMEOUT_SECONDS = 600
BENCHMARK_POLL_INTERVAL_SECONDS = 5.0
BENCHMARK_SETTLE_SECONDS = 30.0
BENCHMARK_MAX_EVENTS = 100
OPENHANDS_CONVERSATION_GET_TIMEOUT_SECONDS = 360.0
OPENHANDS_CONVERSATION_EVENTS_TIMEOUT_SECONDS = 360.0
OPENHANDS_CONVERSATION_DELETE_TIMEOUT_SECONDS = 360.0
OPENHANDS_POLL_TIMEOUT_WARN_EVERY = 3
OPENHANDS_CREATE_MAX_RETRIES = 3
OPENHANDS_CREATE_RETRY_BACKOFF_SECONDS = 2.0
TERMINAL_CONVERSATION_STATUSES = {
    "ERROR",
    "FINISHED",
    "FAILED",
    "MISSING",
    "PAUSED",
    "STOPPED",
    "CANCELLED",
}
IDLE_CONVERSATION_STATUSES = {
    "AWAITING_USER_INPUT",
    "AWAITING_USER_CONFIRMATION",
}
CODE_BLOCK_PATTERN = re.compile(r"```")
REFUSAL_PATTERNS = (
    r"\bI\s+can't\s+help\b",
    r"\bI\s+cannot\s+help\b",
    r"\bI\s+can't\s+assist\b",
    r"\bI\s+cannot\s+assist\b",
    r"\bI\s+cannot\s+provide\b",
    r"\bI\s+won't\s+provide\b",
    r"\bAs an AI language model\b",
)
ERROR_PATTERNS = (
    r"\bTraceback \(most recent call last\):",
    r"\bException:\b",
    r"\bError:\b",
    r"\bfailed to\b",
    r"\bnot found\b",
    r"\binvalid\b",
)
TIMEOUT_PATTERNS = (
    r"\btimed out\b",
    r"\btimeout\b",
    r"\bexceeded the time limit\b",
)
TRUNCATION_PATTERNS = (
    r"\btruncated\b",
    r"\bincomplete\b",
    r"\bto be continued\b",
)


@dataclass(frozen=True)
class PromptSpec:
    id: str
    category: str
    prompt: str


@dataclass(frozen=True)
class ConversationLifecycleResponse:
    status: str
    conversation_id: str
    message: str | None
    conversation_status: str | None


def log(message: str) -> None:
    print(f"[benchmark] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[benchmark] WARNING: {message}", file=sys.stderr, flush=True)


def current_hardware_label() -> str:
    machine = platform.machine() or "unknown-machine"
    processor = platform.processor() or "unknown-processor"
    system = platform.system() or "unknown-os"
    release = platform.release() or "unknown-release"
    return f"{system} {release} / {machine} / {processor}"


def extract_model_name_from_entry(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None

    direct_name = entry.get("name")
    if isinstance(direct_name, str) and direct_name.strip():
        return direct_name.strip()

    config = entry.get("config")
    if isinstance(config, dict):
        config_name = config.get("name")
        if isinstance(config_name, str) and config_name.strip():
            return config_name.strip()

        base_path = config.get("base_path")
        if isinstance(base_path, str) and base_path.strip():
            return Path(base_path).name

    base_path = entry.get("base_path")
    if isinstance(base_path, str) and base_path.strip():
        return Path(base_path).name

    return None


def read_active_model_name() -> str | None:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        warn(f"missing OVMS config at {CONFIG_PATH}; running docker compose normally")
        return None
    except json.JSONDecodeError as exc:
        warn(f"malformed OVMS config at {CONFIG_PATH}: {exc}; running docker compose normally")
        return None
    except OSError as exc:
        warn(f"could not read {CONFIG_PATH}: {exc}; running docker compose normally")
        return None

    if not isinstance(config, dict):
        warn(f"{CONFIG_PATH} does not contain a JSON object; running docker compose normally")
        return None

    for list_name in ("mediapipe_config_list", "model_config_list"):
        entries = config.get(list_name)
        if not isinstance(entries, list):
            continue

        for entry in entries:
            model_name = extract_model_name_from_entry(entry)
            if model_name:
                return model_name

    warn(f"no model name found in {CONFIG_PATH}; running docker compose normally")
    return None


def read_tested_models() -> set[str]:
    try:
        with TESTED_MODELS_LOG.open("r", encoding="utf-8") as tested_file:
            return {line.strip() for line in tested_file if line.strip()}
    except FileNotFoundError:
        return set()
    except OSError as exc:
        warn(f"could not read {TESTED_MODELS_LOG}: {exc}; treating model as untested")
        return set()


def append_tested_model(model_name: str) -> None:
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    with TESTED_MODELS_LOG.open("a", encoding="utf-8") as tested_file:
        tested_file.write(f"{model_name}\n")


def safe_folder_name(model_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", model_name).strip("-._")
    return cleaned or "unknown-model"


def _session_created_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_folder_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(payload, json_file, indent=2)
        json_file.write("\n")


def hash_file_contents(path: Path) -> str:
    """Return the SHA256 hex digest of the given file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_session_metadata(
    session_dir: Path,
    model_name: str,
    created_at: str,
    *,
    prompt_dataset: Path | None = None,
    prompt_dataset_hash: str | None = None,
    openhands_version: str = "unknown",
) -> None:
    metadata = {
        "workflow": BENCHMARK_WORKFLOW,
        "benchmark_version": BENCHMARK_VERSION,
        "model_name": model_name,
        "openhands_base_url": OPENHANDS_URL,
        "openhands_version": openhands_version,
        "created_at": created_at,
        "hardware": current_hardware_label(),
        "prompt_dataset": str(prompt_dataset) if prompt_dataset else None,
        "prompt_dataset_hash": prompt_dataset_hash,
        "start_py_version": platform.python_version(),
        "session_dir": str(session_dir),
    }
    write_json_file(session_dir / "benchmark_metadata.json", metadata)


def write_session_summary(session_dir: Path, model_name: str) -> None:
    summary = f"""# Benchmark Session

Model: `{model_name}`

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
"""
    (session_dir / "run_summary.md").write_text(summary, encoding="utf-8")


def create_benchmark_dir(model_name: str, created_at: str | None = None) -> Path:
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    session_stamp = _session_folder_stamp()
    session_dir = BENCHMARKS_DIR / f"{safe_folder_name(model_name)}_{session_stamp}"
    suffix = 2
    while session_dir.exists():
        session_dir = BENCHMARKS_DIR / f"{safe_folder_name(model_name)}_{session_stamp}_{suffix}"
        suffix += 1

    session_dir.mkdir(parents=True, exist_ok=False)
    (session_dir / "task_transcripts").mkdir(parents=True, exist_ok=True)
    created_at = created_at or _session_created_at()
    write_session_metadata(session_dir, model_name, created_at)
    write_session_summary(session_dir, model_name)
    write_latest_session(model_name, session_dir)
    return session_dir


def write_latest_session(model_name: str, session_dir: Path) -> None:
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    latest = {
        "model_name": model_name,
        "session_dir": str(session_dir),
        "updated_at": _session_created_at(),
    }
    write_json_file(LATEST_SESSION_FILE, latest)


def read_latest_session(model_name: str) -> Path | None:
    try:
        with LATEST_SESSION_FILE.open("r", encoding="utf-8") as latest_file:
            latest = json.load(latest_file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(latest, dict) or latest.get("model_name") != model_name:
        return None

    session_dir = latest.get("session_dir")
    if not isinstance(session_dir, str):
        return None

    path = Path(session_dir)
    return path if path.is_dir() else None


def find_latest_session(model_name: str) -> Path | None:
    latest_from_pointer = read_latest_session(model_name)
    if latest_from_pointer:
        return latest_from_pointer

    safe_name = safe_folder_name(model_name)
    candidates = [path for path in BENCHMARKS_DIR.glob(f"{safe_name}_*") if path.is_dir()]
    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


def get_or_create_session_dir(model_name: str) -> Path:
    session_dir = find_latest_session(model_name)
    if session_dir:
        if not (session_dir / "benchmark_metadata.json").exists():
            write_session_metadata(session_dir, model_name, _session_created_at())
        if not (session_dir / "run_summary.md").exists():
            write_session_summary(session_dir, model_name)
        write_latest_session(model_name, session_dir)
        return session_dir

    return create_benchmark_dir(model_name)


def build_prompt_dataset_hash(prompt_dataset: Path) -> str:
    return hash_file_contents(prompt_dataset)


def ensure_session_dir_writable(session_dir: Path) -> None:
    test_path = session_dir / ".write_test"
    try:
        test_path.write_text("ok\n", encoding="utf-8")
        test_path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Benchmark session directory is not writable: {session_dir}") from exc


def ensure_docker_telemetry_available() -> None:
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", STATS_FORMAT],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "Docker telemetry is unavailable. Ensure Docker is running before benchmarking."
        ) from exc

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "docker stats failed"
        raise RuntimeError(f"Docker telemetry is unavailable: {message}")


def ensure_ovms_health() -> None:
    try:
        response = requests.get(OVMS_URL, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(
            f"OVMS is not reachable at {OVMS_URL}. Start it first with: bash scripts/deploy_ovms.sh"
        ) from exc

    if response.status_code >= 500:
        raise RuntimeError(
            f"OVMS returned HTTP {response.status_code} at {OVMS_URL}. Start it first with: bash scripts/deploy_ovms.sh"
        )


def detect_openhands_version() -> str:
    inspect_commands = [
        [
            "docker",
            "inspect",
            OPENHANDS_CONTAINER,
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.version"}}',
        ],
        ["docker", "inspect", OPENHANDS_CONTAINER, "--format", "{{.Config.Image}}"],
    ]

    for command in inspect_commands:
        try:
            result = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue

        if result.returncode != 0:
            continue

        value = result.stdout.strip()
        if not value or value == "<no value>":
            continue

        if command[-1] != "{{.Config.Image}}":
            return value

        if "@" in value:
            continue

        if ":" in value:
            candidate = value.rsplit(":", maxsplit=1)[-1].strip()
            if candidate and candidate.lower() != "latest":
                return candidate

    return "unknown"


def ensure_openhands_health(client: "OpenHandsClient") -> None:
    try:
        response = requests.get(OPENHANDS_URL, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(f"OpenHands is not reachable at {OPENHANDS_URL}") from exc

    if response.status_code >= 500:
        raise RuntimeError(f"OpenHands returned HTTP {response.status_code} at {OPENHANDS_URL}")

    client.ensure_conversation_api_reachable()


def is_coding_prompt(category: str) -> bool:
    normalized = category.lower()
    return normalized.startswith(("code", "coding", "debug", "fix", "implement"))


def detect_refusal(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in REFUSAL_PATTERNS)


def detect_error(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in ERROR_PATTERNS)


def detect_timeout(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in TIMEOUT_PATTERNS)


def detect_truncation(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in TRUNCATION_PATTERNS) or text.endswith(("...", "…"))


def response_has_code_block(text: str) -> bool:
    return bool(CODE_BLOCK_PATTERN.search(text))


def score_prompt_quality(prompt: PromptSpec, assistant_reply: str, timed_out: bool) -> float:
    reply = assistant_reply.strip()
    if not reply:
        return 0.0

    score = 0.0
    score += 0.20
    score += min(len(reply) / 1600.0, 1.0) * 0.25

    if not detect_truncation(reply) and len(reply) >= 60:
        score += 0.20

    if is_coding_prompt(prompt.category) and response_has_code_block(reply):
        score += 0.15

    if not detect_refusal(reply):
        score += 0.10
    else:
        score -= 0.30

    if not detect_error(reply):
        score += 0.10
    else:
        score -= 0.30

    if not timed_out and not detect_timeout(reply):
        score += 0.10
    else:
        score -= 0.35

    return round(max(0.0, min(1.0, score)), 4)


def format_quality_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_event_timeline(events: list[Any]) -> str:
    if not events:
        return "- No events recorded."

    lines = []
    for index, event in enumerate(events, start=1):
        lines.append(f"{index}. {event_summary(event)}")
    return "\n".join(lines)


def write_task_transcript(session_dir: Path, result: dict[str, Any], events: list[Any]) -> None:
    transcripts_dir = session_dir / "task_transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    prompt_id = safe_folder_name(str(result.get("id") or "prompt"))
    category = safe_folder_name(str(result.get("category") or "general"))
    transcript_path = transcripts_dir / f"{category}_{prompt_id}.md"
    final_response = result.get("assistant_reply") or ""
    conversation_id = result.get("conversation_id") or "n/a"
    content = f"""# Task Transcript

Prompt ID: `{result.get('id', 'unknown')}`
Category: `{result.get('category', 'unknown')}`

## Prompt

```text
{result.get('prompt', '')}
```

## Conversation ID

`{conversation_id}`

## Event Timeline

{format_event_timeline(events)}

## Final Response

```text
{final_response}
```

## Result

- Success: `{str(bool(result.get('success'))).lower()}`
- Quality Score: `{format_quality_score(result.get('quality_score'))}`
- Response Length: `{result.get('response_length', 0)}`
"""
    transcript_path.write_text(content, encoding="utf-8")


def validate_benchmark_prerequisites(session_dir: Path, client: "OpenHandsClient") -> str:
    ensure_session_dir_writable(session_dir)
    ensure_docker_telemetry_available()
    ensure_ovms_health()
    ensure_openhands_health(client)
    return detect_openhands_version()


def run_compose_normally() -> int:
    completed = subprocess.run(COMPOSE_COMMAND, cwd=PROJECT_ROOT)
    return completed.returncode


def parse_stats_line(line: str) -> list[str] | None:
    parts = [part.strip() for part in line.split(",", maxsplit=3)]
    if len(parts) != 4:
        return None
    if parts[0] not in TARGET_CONTAINERS:
        return None
    return parts


def collect_docker_stats(stop_event: threading.Event, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["timestamp", "container", "cpu_percent", "memory_usage", "memory_percent"])
        output_file.flush()

        while not stop_event.is_set():
            timestamp = datetime.now().isoformat(timespec="seconds")
            try:
                result = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", STATS_FORMAT],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as exc:
                writer.writerow([timestamp, "docker-stats-error", "", str(exc), ""])
                output_file.flush()
                stop_event.wait(2)
                continue

            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "docker stats failed"
                writer.writerow([timestamp, "docker-stats-error", "", message, ""])
                output_file.flush()
                stop_event.wait(2)
                continue

            for raw_line in result.stdout.splitlines():
                stats = parse_stats_line(raw_line)
                if stats:
                    writer.writerow([timestamp, *stats])
            output_file.flush()
            stop_event.wait(2)


def follow_container_logs(container_name: str, output_path: Path, stop_event: threading.Event) -> None:
    with output_path.open("a", encoding="utf-8", errors="replace") as output_file:
        while not stop_event.is_set():
            try:
                process = subprocess.Popen(
                    ["docker", "logs", "-f", "--timestamps", container_name],
                    cwd=PROJECT_ROOT,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except OSError as exc:
                output_file.write(f"[benchmark] docker logs failed for {container_name}: {exc}\n")
                output_file.flush()
                stop_event.wait(2)
                continue

            while process.poll() is None and not stop_event.is_set():
                stop_event.wait(0.5)

            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

            output_file.flush()
            if not stop_event.is_set():
                stop_event.wait(2)


def start_background_collectors(session_dir: Path) -> tuple[threading.Event, list[threading.Thread]]:
    stop_event = threading.Event()
    collectors = [
        threading.Thread(
            target=collect_docker_stats,
            args=(stop_event, session_dir / "docker_stats.csv"),
            name="docker-stats-collector",
            daemon=True,
        ),
        threading.Thread(
            target=follow_container_logs,
            args=("ovms-llm", session_dir / "ovms_execution.log", stop_event),
            name="ovms-log-collector",
            daemon=True,
        ),
        threading.Thread(
            target=follow_container_logs,
            args=("openhands", session_dir / "openhands_execution.log", stop_event),
            name="openhands-log-collector",
            daemon=True,
        ),
    ]

    for collector in collectors:
        collector.start()

    return stop_event, collectors


def terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            return
    else:
        process.send_signal(signal.CTRL_BREAK_EVENT if hasattr(signal, "CTRL_BREAK_EVENT") else signal.SIGTERM)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_compose_with_collectors(stop_event: threading.Event, collectors: list[threading.Thread]) -> int:
    popen_kwargs: dict[str, Any] = {"cwd": PROJECT_ROOT}
    if os.name == "posix":
        popen_kwargs["preexec_fn"] = os.setsid
    elif os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    compose_process = subprocess.Popen(COMPOSE_COMMAND, **popen_kwargs)

    try:
        return compose_process.wait()
    except KeyboardInterrupt:
        warn("received interrupt; stopping Docker Compose")
        stop_event.set()
        terminate_process(compose_process)
        return 130
    finally:
        stop_event.set()
        for collector in collectors:
            collector.join(timeout=5)


def parse_prompt_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def load_prompt_dataset(prompt_file: Path) -> list[PromptSpec]:
    with prompt_file.open("r", encoding="utf-8") as prompt_stream:
        payload = json.load(prompt_stream)

    if not isinstance(payload, list):
        raise ValueError(f"prompt dataset must be a JSON list: {prompt_file}")

    prompts: list[PromptSpec] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"prompt entry {index} is not an object in {prompt_file}")

        prompt_id = parse_prompt_text(entry.get("id"))
        category = parse_prompt_text(entry.get("category"))
        prompt_text = parse_prompt_text(entry.get("prompt"))
        if not all((prompt_id, category, prompt_text)):
            raise ValueError(
                f"prompt entry {index} is missing id, category, or prompt in {prompt_file}"
            )

        prompts.append(PromptSpec(id=prompt_id, category=category, prompt=prompt_text))

    if not prompts:
        raise ValueError(f"prompt dataset is empty: {prompt_file}")

    return prompts


def resolve_prompt_dataset_path(prompts_argument: str | None) -> Path:
    if prompts_argument is None:
        return DEFAULT_PROMPTS_FILE

    raw_path = Path(prompts_argument).expanduser()
    candidates = [raw_path]
    if not raw_path.is_absolute():
        candidates.extend(
            [
                Path.cwd() / raw_path,
                PROJECT_ROOT / raw_path,
                BENCHMARKS_DIR / raw_path,
            ]
        )

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue

    raise FileNotFoundError(f"prompt dataset not found: {prompts_argument}")


def parse_cpu_percent(value: str) -> float | None:
    cleaned = value.strip().rstrip("%")
    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


_MEMORY_PATTERN = re.compile(r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[KMGTPE]?i?B)", re.IGNORECASE)
_MEMORY_UNIT_TO_BYTES = {
    "B": 1,
    "KB": 1_000,
    "KIB": 1_024,
    "MB": 1_000_000,
    "MIB": 1_048_576,
    "GB": 1_000_000_000,
    "GIB": 1_073_741_824,
    "TB": 1_000_000_000_000,
    "TIB": 1_099_511_627_776,
    "PB": 1_000_000_000_000_000,
    "PIB": 1_125_899_906_842_624,
    "EB": 1_000_000_000_000_000_000,
    "EIB": 1_152_921_504_606_846_976,
}


def parse_memory_usage_mb(value: str) -> float | None:
    usage_part = value.split("/", maxsplit=1)[0].strip()
    if not usage_part:
        return None

    match = _MEMORY_PATTERN.search(usage_part)
    if not match:
        return None

    raw_value = match.group("value")
    raw_unit = match.group("unit").upper()
    try:
        numeric_value = float(raw_value)
    except ValueError:
        return None

    bytes_per_unit = _MEMORY_UNIT_TO_BYTES.get(raw_unit)
    if bytes_per_unit is None:
        return None

    return numeric_value * bytes_per_unit / 1_000_000


def compute_resource_metrics(stats_path: Path) -> dict[str, float]:
    sample_totals: dict[str, dict[str, float]] = {}
    if not stats_path.is_file():
        return {
            "cpu_peak_percent": 0.0,
            "memory_peak_mb": 0.0,
            "avg_cpu_percent": 0.0,
            "avg_memory_mb": 0.0,
        }

    with stats_path.open("r", encoding="utf-8", newline="") as stats_file:
        reader = csv.DictReader(stats_file)
        for row in reader:
            container = (row.get("container") or "").strip()
            if container not in TARGET_CONTAINERS:
                continue

            timestamp = (row.get("timestamp") or "").strip()
            if not timestamp:
                continue

            cpu_percent = parse_cpu_percent(row.get("cpu_percent") or "")
            memory_mb = parse_memory_usage_mb(row.get("memory_usage") or "")
            if cpu_percent is None and memory_mb is None:
                continue

            sample = sample_totals.setdefault(timestamp, {"cpu": 0.0, "memory": 0.0})
            if cpu_percent is not None:
                sample["cpu"] += cpu_percent
            if memory_mb is not None:
                sample["memory"] += memory_mb

    if not sample_totals:
        return {
            "cpu_peak_percent": 0.0,
            "memory_peak_mb": 0.0,
            "avg_cpu_percent": 0.0,
            "avg_memory_mb": 0.0,
        }

    cpu_values = [sample["cpu"] for sample in sample_totals.values()]
    memory_values = [sample["memory"] for sample in sample_totals.values()]
    return {
        "cpu_peak_percent": round(max(cpu_values), 4),
        "memory_peak_mb": round(max(memory_values), 4),
        "avg_cpu_percent": round(sum(cpu_values) / len(cpu_values), 4),
        "avg_memory_mb": round(sum(memory_values) / len(memory_values), 4),
    }


def format_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}s"


def format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def format_mb(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} MB"


def extract_text_block(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""

    pieces: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            text = block["text"].strip()
            if text:
                pieces.append(text)
        elif isinstance(block, str) and block.strip():
            pieces.append(block.strip())
    return "\n".join(pieces).strip()


def event_summary(event: Any) -> str:
    if not isinstance(event, dict):
        return "unknown"

    source = event.get("source")
    if source == "user":
        message = event.get("llm_message")
        if isinstance(message, dict):
            content = extract_text_block(message.get("content"))
            if content:
                return f"user:{content[:80]}"
        return "user"

    if source == "agent":
        llm_message = event.get("llm_message")
        if isinstance(llm_message, dict):
            content = extract_text_block(llm_message.get("content"))
            if content:
                return f"agent:{content[:80]}"

        thought = extract_text_block(event.get("thought"))
        if thought:
            return f"agent-thought:{thought[:80]}"

        action = event.get("action")
        if isinstance(action, dict) and isinstance(action.get("kind"), str):
            return f"agent-action:{action['kind']}"

        return "agent"

    if source == "environment":
        observation = event.get("observation")
        if isinstance(observation, dict) and isinstance(observation.get("kind"), str):
            return f"environment:{observation['kind']}"
        return "environment"

    return str(source or "unknown")


def extract_final_assistant_reply(events: list[Any]) -> str:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue

        if event.get("source") != "agent":
            continue

        llm_message = event.get("llm_message")
        if isinstance(llm_message, dict):
            content = extract_text_block(llm_message.get("content"))
            if content:
                return content

        thought = extract_text_block(event.get("thought"))
        if thought:
            return thought

        message = event.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    return ""


def build_event_timeline(events: list[Any], limit: int = 6) -> str:
    timeline = [event_summary(event) for event in events[-limit:]]
    return " | ".join(timeline)


def normalize_conversation_status(value: Any) -> str:
    if isinstance(value, str):
        return value.upper().strip()
    return ""


def is_conversation_terminal(status: str) -> bool:
    return status in {"STOPPED", "ARCHIVED", "ERROR"}


def is_runtime_ready(status: str) -> bool:
    return status == "STATUS$READY"


def is_runtime_error(status: str) -> bool:
    return status.startswith("STATUS$ERROR") or status == "STATUS$GIT_PROVIDER_AUTHENTICATION_ERROR"


def format_openhands_contract_summary(openapi: dict[str, Any] | None) -> str:
    if not isinstance(openapi, dict):
        return "Detected contract: unavailable"

    info = openapi.get("info") if isinstance(openapi.get("info"), dict) else {}
    version = info.get("version") if isinstance(info, dict) else None
    paths = openapi.get("paths") if isinstance(openapi.get("paths"), dict) else {}
    discovered = [
        path
        for path in (
            "/api/conversations",
            "/api/conversations/{conversation_id}",
            "/api/conversations/{conversation_id}/start",
            "/api/conversations/{conversation_id}/events",
        )
        if path in paths
    ]
    contract_lines = [f"OpenHands API version: {version or 'unknown'}", "Discovered routes:"]
    contract_lines.extend(f"- {path}" for path in discovered)
    return "\n".join(contract_lines)


class OpenHandsClient:
    def __init__(self, base_url: str = OPENHANDS_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self.session.get(self._url(path), params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            # Some endpoints may return an empty body or plain text on error; return
            # the raw text so callers can produce a clear runtime error instead
            # of an unhandled JSON decode traceback.
            return response.text

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> Any:
        response = self.session.post(self._url(path), json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _parse_lifecycle_response(self, data: Any) -> ConversationLifecycleResponse:
        if not isinstance(data, dict):
            raise RuntimeError(f"OpenHands returned an unexpected payload: {data!r}")

        return ConversationLifecycleResponse(
            status=str(data.get("status", "")),
            conversation_id=str(data.get("conversation_id", "")),
            message=data.get("message") if isinstance(data.get("message"), str) else None,
            conversation_status=normalize_conversation_status(data.get("conversation_status")) or None,
        )

    def _conversation_healthcheck_error(
        self,
        *,
        stage: str,
        openapi: dict[str, Any] | None,
        detail: str,
        conversation_id: str | None = None,
        conversation: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> RuntimeError:
        lines = [
            f"OpenHands reachable at {self.base_url}",
            "Expected JSON API response.",
            format_openhands_contract_summary(openapi),
            f"Healthcheck stage: {stage}",
            f"Details: {detail}",
        ]
        if conversation_id:
            lines.append(f"Conversation ID: {conversation_id}")
        if conversation:
            lines.append(
                "Conversation info: "
                + json.dumps(
                    {
                        "status": conversation.get("status"),
                        "runtime_status": conversation.get("runtime_status"),
                        "url": conversation.get("url"),
                    },
                    sort_keys=True,
                )
            )
        if events is not None:
            lines.append(f"Event count: {len(events)}")
        lines.append("Suggested fix: update the OpenHands client to the discovered /api/conversations contract.")
        return RuntimeError("\n".join(lines))

    def ensure_conversation_api_reachable(self) -> None:
        openapi = self._get_json("/openapi.json", timeout=10.0)
        if not isinstance(openapi, dict):
            raise self._conversation_healthcheck_error(
                stage="openapi discovery",
                openapi=None,
                detail=f"Expected JSON from /openapi.json but received {type(openapi).__name__}",
            )

        paths = openapi.get("paths") if isinstance(openapi.get("paths"), dict) else {}
        required_paths = [
            "/api/conversations",
            "/api/conversations/{conversation_id}",
            "/api/conversations/{conversation_id}/start",
            "/api/conversations/{conversation_id}/events",
        ]
        missing_paths = [path for path in required_paths if path not in paths]
        if missing_paths:
            raise self._conversation_healthcheck_error(
                stage="openapi route validation",
                openapi=openapi,
                detail=f"Missing required routes: {', '.join(missing_paths)}",
            )

        probe_prompt = PromptSpec(
            id="openhands-healthcheck",
            category="system",
            prompt="OpenHands API healthcheck. Do not perform any destructive actions.",
        )
        created: ConversationLifecycleResponse | None = None
        conversation: dict[str, Any] | None = None
        events: list[dict[str, Any]] | None = None
        try:
            created = self.create_conversation(probe_prompt)
            if not created.conversation_id:
                raise self._conversation_healthcheck_error(
                    stage="conversation creation",
                    openapi=openapi,
                    detail="Conversation creation returned no conversation_id",
                )

            started = self.start_conversation(created.conversation_id)
            conversation = self.get_conversation(created.conversation_id)
            if conversation is None:
                raise self._conversation_healthcheck_error(
                    stage="conversation retrieval",
                    openapi=openapi,
                    detail="Created conversation could not be fetched back from /api/conversations/{conversation_id}",
                    conversation_id=created.conversation_id,
                )

            events = self.search_events(created.conversation_id, limit=5)
            if not isinstance(events, list):
                raise self._conversation_healthcheck_error(
                    stage="event retrieval",
                    openapi=openapi,
                    detail="Event retrieval did not return a JSON list",
                    conversation_id=created.conversation_id,
                    conversation=conversation,
                )

            if not started.conversation_id:
                raise self._conversation_healthcheck_error(
                    stage="conversation start",
                    openapi=openapi,
                    detail="Conversation start response did not include a conversation_id",
                    conversation_id=created.conversation_id,
                    conversation=conversation,
                    events=events,
                )
        except RuntimeError:
            raise
        except Exception as exc:
            raise self._conversation_healthcheck_error(
                stage="conversation probe",
                openapi=openapi,
                detail=str(exc),
                conversation_id=created.conversation_id if created else None,
                conversation=conversation,
                events=events,
            ) from exc
        finally:
            if created and created.conversation_id:
                try:
                    self.delete_conversation(created.conversation_id)
                except Exception:
                    pass

    def create_conversation(self, prompt: PromptSpec, model_name: str | None = None) -> ConversationLifecycleResponse:
        payload = {
            "repository": None,
            "git_provider": None,
            "selected_branch": None,
            "initial_user_msg": prompt.prompt,
            "image_urls": None,
            "replay_json": None,
            "suggested_task": None,
            "create_microagent": None,
            "conversation_instructions": None,
            "mcp_config": None,
        }
        last_exc: Exception | None = None
        for attempt in range(1, OPENHANDS_CREATE_MAX_RETRIES + 1):
            try:
                data = self._post_json("/api/conversations", payload, timeout=360.0)
                return self._parse_lifecycle_response(data)
            except (requests.ReadTimeout, requests.ConnectTimeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt >= OPENHANDS_CREATE_MAX_RETRIES:
                    break
                warn(
                    "OpenHands conversation creation timed out "
                    f"(attempt {attempt}/{OPENHANDS_CREATE_MAX_RETRIES}); retrying in {OPENHANDS_CREATE_RETRY_BACKOFF_SECONDS:.1f}s"
                )
                time.sleep(OPENHANDS_CREATE_RETRY_BACKOFF_SECONDS)

        if last_exc is not None:
            raise last_exc

        data = self._post_json("/api/conversations", payload, timeout=360.0)
        return self._parse_lifecycle_response(data)

    def start_conversation(self, conversation_id: str) -> ConversationLifecycleResponse:
        data = self._post_json(
            f"/api/conversations/{conversation_id}/start",
            {"providers_set": []},
            timeout=360.0,
        )
        return self._parse_lifecycle_response(data)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        data = self._get_json(
            f"/api/conversations/{conversation_id}",
            timeout=OPENHANDS_CONVERSATION_GET_TIMEOUT_SECONDS,
        )
        if not isinstance(data, dict):
            return None
        return data

    def search_events(
        self,
        conversation_id: str,
        limit: int = BENCHMARK_MAX_EVENTS,
        start_id: int = 0,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        effective_limit = min(limit, BENCHMARK_MAX_EVENTS)
        data = self._get_json(
            f"/api/conversations/{conversation_id}/events",
            params={"start_id": start_id, "limit": effective_limit, "reverse": reverse},
            timeout=OPENHANDS_CONVERSATION_EVENTS_TIMEOUT_SECONDS,
        )
        if not isinstance(data, dict):
            return []

        items = data.get("events")
        if not isinstance(items, list):
            return []

        return [item for item in items if isinstance(item, dict)]

    def delete_conversation(self, conversation_id: str) -> None:
        response = self.session.delete(
            self._url(f"/api/conversations/{conversation_id}"),
            timeout=OPENHANDS_CONVERSATION_DELETE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


def wait_for_conversation_settle(
    client: OpenHandsClient,
    conversation_id: str,
) -> dict[str, Any]:
    started_at = time.monotonic()
    stable_since = started_at
    ready_at: float | None = None
    assistant_reply_seen_at: float | None = None
    last_event_count = -1
    last_events: list[dict[str, Any]] = []
    last_conversation: dict[str, Any] | None = None
    last_conversation_status = ""
    last_runtime_status = ""
    consecutive_conversation_timeouts = 0
    consecutive_event_timeouts = 0

    while time.monotonic() - started_at < BENCHMARK_CONVERSATION_TIMEOUT_SECONDS:
        try:
            current_conversation = client.get_conversation(conversation_id)
        except requests.ReadTimeout as exc:
            consecutive_conversation_timeouts += 1
            if consecutive_conversation_timeouts % OPENHANDS_POLL_TIMEOUT_WARN_EVERY == 0:
                warn(
                    "conversation status unavailable "
                    f"for {conversation_id} ({consecutive_conversation_timeouts} consecutive read timeouts): {exc}"
                )
            if last_conversation is None:
                raise RuntimeError(f"OpenHands conversation unavailable for {conversation_id}: {exc}") from exc
            current_conversation = last_conversation
        except requests.RequestException as exc:
            if last_conversation is None:
                raise RuntimeError(f"OpenHands conversation unavailable for {conversation_id}: {exc}") from exc
            warn(f"conversation status unavailable for {conversation_id}: {exc}")
            current_conversation = last_conversation
        else:
            consecutive_conversation_timeouts = 0

        last_conversation = current_conversation
        if last_conversation is None:
            raise RuntimeError(f"OpenHands conversation not found: {conversation_id}")

        last_conversation_status = normalize_conversation_status(last_conversation.get("status"))
        last_runtime_status = normalize_conversation_status(last_conversation.get("runtime_status"))

        if ready_at is None and (is_runtime_ready(last_runtime_status) or last_conversation_status == "RUNNING"):
            ready_at = time.monotonic()

        try:
            events = client.search_events(conversation_id)
            consecutive_event_timeouts = 0
        except requests.ReadTimeout as exc:
            consecutive_event_timeouts += 1
            if consecutive_event_timeouts % OPENHANDS_POLL_TIMEOUT_WARN_EVERY == 0:
                warn(
                    "event history unavailable "
                    f"for {conversation_id} ({consecutive_event_timeouts} consecutive read timeouts): {exc}"
                )
            events = last_events
        except requests.RequestException as exc:
            consecutive_event_timeouts = 0
            warn(f"event history unavailable for {conversation_id}: {exc}")
            events = last_events

        if extract_final_assistant_reply(events):
            if assistant_reply_seen_at is None:
                assistant_reply_seen_at = time.monotonic()

        event_count = len(events)
        if event_count != last_event_count:
            last_event_count = event_count
            last_events = events
            stable_since = time.monotonic()

        time_since_stable = time.monotonic() - stable_since
        if time_since_stable >= BENCHMARK_SETTLE_SECONDS:
            if (
                last_conversation_status in TERMINAL_CONVERSATION_STATUSES
                or last_conversation_status in IDLE_CONVERSATION_STATUSES
                or is_runtime_error(last_runtime_status)
            ):
                break

        time.sleep(BENCHMARK_POLL_INTERVAL_SECONDS)

    return {
        "conversation": last_conversation,
        "conversation_status": last_conversation_status,
        "runtime_status": last_runtime_status,
        "events": last_events,
        "ready_seconds": round((ready_at or time.monotonic()) - started_at, 4),
        "timed_out": time.monotonic() - started_at >= BENCHMARK_CONVERSATION_TIMEOUT_SECONDS,
        "elapsed_seconds": round(time.monotonic() - started_at, 4),
    }


def run_openhands_prompt(
    client: OpenHandsClient,
    session_dir: Path,
    model_name: str,
    prompt: PromptSpec,
) -> dict[str, Any]:
    started_at = _session_created_at()
    start_time = time.monotonic()

    def finalize_result(result: dict[str, Any], events: list[Any] | None = None) -> dict[str, Any]:
        event_list = events or []
        assistant_reply = parse_prompt_text(result.get("assistant_reply"))
        quality_score = score_prompt_quality(prompt, assistant_reply, bool(result.get("timed_out")))
        finalized = {
            **result,
            "assistant_reply": assistant_reply,
            "response_length": len(assistant_reply),
            "quality_score": quality_score,
        }
        success = bool(assistant_reply) and quality_score >= 0.55 and not result.get("timed_out")
        if detect_refusal(assistant_reply) or detect_error(assistant_reply) or detect_timeout(assistant_reply):
            success = False
        finalized["success"] = success
        finalized.setdefault(
            "error",
            None if success else ("timed out waiting for a stable OpenHands response" if result.get("timed_out") else "no assistant response detected"),
        )
        finalized["event_timeline"] = build_event_timeline(event_list)
        write_task_transcript(session_dir, finalized, event_list)
        return finalized

    try:
        created = client.create_conversation(prompt, model_name)
    except requests.RequestException as exc:
        return finalize_result(
            {
                "id": prompt.id,
                "category": prompt.category,
                "prompt": prompt.prompt,
                "model_name": model_name,
                "started_at": started_at,
                "success": False,
                "assistant_reply": "",
                "event_count": 0,
                "event_timeline": "",
                "timed_out": False,
                "error": f"failed to create OpenHands conversation: {exc}",
            },
            [],
        )

    try:
        started = client.start_conversation(created.conversation_id)
    except Exception as exc:
        return finalize_result(
            {
                "id": prompt.id,
                "category": prompt.category,
                "prompt": prompt.prompt,
                "model_name": model_name,
                "started_at": started_at,
                "conversation_id": created.conversation_id,
                "success": False,
                "assistant_reply": "",
                "event_count": 0,
                "event_timeline": "",
                "timed_out": False,
                "error": str(exc),
            },
            [],
        )

    conversation_id = created.conversation_id
    settle_result = wait_for_conversation_settle(client, conversation_id)
    events = settle_result["events"]
    assistant_reply = extract_final_assistant_reply(events)

    return finalize_result({
        "id": prompt.id,
        "category": prompt.category,
        "prompt": prompt.prompt,
        "model_name": model_name,
        "started_at": started_at,
        "api_status": started.status or created.status,
        "conversation_id": conversation_id,
        "status": settle_result["conversation_status"],
        "runtime_status": settle_result["runtime_status"],
        "ready_seconds": settle_result["ready_seconds"],
        "settle_seconds": settle_result["elapsed_seconds"],
        "event_count": len(events),
        "assistant_reply": assistant_reply,
        "timed_out": settle_result["timed_out"],
        "error": None,
    }, events)


def summarize_prompt_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    return {
        "id": result.get("id"),
        "category": result.get("category"),
        "quality_score": result.get("quality_score"),
        "success": bool(result.get("success")),
        "response_length": len(result.get("assistant_reply", "")) if isinstance(result.get("assistant_reply"), str) else 0,
        "conversation_id": result.get("conversation_id"),
    }


def summarize_prompt_results(
    model_name: str,
    prompt_dataset: Path,
    session_dir: Path,
    prompt_results: list[dict[str, Any]],
    resource_metrics: dict[str, float],
    prompt_dataset_hash: str,
    openhands_version: str,
    created_at: str,
    completed_at: str,
    error: str | None = None,
) -> dict[str, Any]:
    prompt_count = len(prompt_results)
    ready_latencies = [result["ready_seconds"] for result in prompt_results if isinstance(result.get("ready_seconds"), (int, float))]
    settle_latencies = [result["settle_seconds"] for result in prompt_results if isinstance(result.get("settle_seconds"), (int, float))]
    quality_scores = [result["quality_score"] for result in prompt_results if isinstance(result.get("quality_score"), (int, float))]
    response_lengths = [len(result.get("assistant_reply", "")) for result in prompt_results if isinstance(result.get("assistant_reply"), str)]
    successful_requests = sum(1 for result in prompt_results if result.get("success"))

    top_prompt = max(prompt_results, key=lambda result: float(result.get("quality_score", 0.0) or 0.0), default=None)
    worst_prompt = min(prompt_results, key=lambda result: float(result.get("quality_score", 0.0) or 0.0), default=None)

    summary: dict[str, Any] = {
        "workflow": BENCHMARK_WORKFLOW,
        "benchmark_version": BENCHMARK_VERSION,
        "model_name": model_name,
        "session_dir": str(session_dir),
        "prompt_dataset": str(prompt_dataset),
        "prompt_dataset_hash": prompt_dataset_hash,
        "openhands_version": openhands_version,
        "start_py_version": platform.python_version(),
        "created_at": created_at,
        "completed_at": completed_at,
        "prompt_count": prompt_count,
        "successful_requests": successful_requests,
        "success_rate": round(successful_requests / prompt_count, 4) if prompt_count else 0.0,
        "avg_quality_score": round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else 0.0,
        "avg_ready_seconds": round(sum(ready_latencies) / len(ready_latencies), 4) if ready_latencies else None,
        "avg_settle_seconds": round(sum(settle_latencies) / len(settle_latencies), 4) if settle_latencies else None,
        "avg_response_length": round(sum(response_lengths) / len(response_lengths), 4) if response_lengths else None,
        "hardware": current_hardware_label(),
        "resource_metrics": resource_metrics,
        "top_prompt": summarize_prompt_result(top_prompt) if top_prompt else None,
        "worst_prompt": summarize_prompt_result(worst_prompt) if worst_prompt else None,
    }

    if error:
        summary["error"] = error

    return summary


def write_benchmark_results(
    session_dir: Path,
    model_name: str,
    prompt_dataset: Path,
    prompt_dataset_hash: str,
    created_at: str,
    prompt_results: list[dict[str, Any]],
) -> None:
    payload = {
        "workflow": BENCHMARK_WORKFLOW,
        "benchmark_version": BENCHMARK_VERSION,
        "model_name": model_name,
        "prompt_dataset": str(prompt_dataset),
        "prompt_dataset_hash": prompt_dataset_hash,
        "created_at": created_at,
        "results": prompt_results,
    }
    write_json_file(session_dir / BENCHMARK_RESULTS_FILE, payload)


def write_benchmark_summary(session_dir: Path, summary: dict[str, Any]) -> None:
    write_json_file(session_dir / BENCHMARK_SUMMARY_FILE, summary)


def format_benchmark_report(summary: dict[str, Any]) -> str:
    resource_metrics = summary.get("resource_metrics") or {}
    top_prompt = summary.get("top_prompt") or {}
    worst_prompt = summary.get("worst_prompt") or {}
    lines = [
        "# OpenHands Benchmark Report",
        "",
        f"Model: `{summary.get('model_name', 'unknown-model')}`",
        f"Hardware: `{summary.get('hardware', 'unknown')}`",
        f"Date: `{summary.get('completed_at', 'unknown')}`",
        f"Prompt Count: `{summary.get('prompt_count', 0)}`",
        f"OpenHands Version: `{summary.get('openhands_version', 'unknown')}`",
        f"Benchmark Version: `{summary.get('benchmark_version', '1.0')}`",
        f"Prompt Dataset: `{summary.get('prompt_dataset', 'unknown')}`",
        f"Prompt Dataset Hash: `{summary.get('prompt_dataset_hash', 'unknown')}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Success Rate | {format_percent((summary.get('success_rate', 0.0) or 0.0) * 100)} |",
        f"| Average Quality Score | {format_quality_score(summary.get('avg_quality_score'))} |",
        f"| Average Time to READY | {format_seconds(summary.get('avg_ready_seconds'))} |",
        f"| Average Time to Settle | {format_seconds(summary.get('avg_settle_seconds'))} |",
        f"| Peak Memory | {format_mb(resource_metrics.get('memory_peak_mb'))} |",
        f"| Peak CPU | {format_percent(resource_metrics.get('cpu_peak_percent'))} |",
        "",
        "## Top Performing Prompt",
        "",
        f"- Prompt ID: `{top_prompt.get('id', 'n/a')}`",
        f"- Category: `{top_prompt.get('category', 'n/a')}`",
        f"- Quality Score: `{format_quality_score(top_prompt.get('quality_score'))}`",
        f"- Success: `{str(bool(top_prompt.get('success'))).lower()}`",
        "",
        "## Worst Performing Prompt",
        "",
        f"- Prompt ID: `{worst_prompt.get('id', 'n/a')}`",
        f"- Category: `{worst_prompt.get('category', 'n/a')}`",
        f"- Quality Score: `{format_quality_score(worst_prompt.get('quality_score'))}`",
        f"- Success: `{str(bool(worst_prompt.get('success'))).lower()}`",
    ]

    return "\n".join(lines) + "\n"


def write_benchmark_report(session_dir: Path, summary: dict[str, Any]) -> None:
    (session_dir / BENCHMARK_REPORT_FILE).write_text(format_benchmark_report(summary), encoding="utf-8")


def write_failure_artifacts(
    session_dir: Path,
    model_name: str,
    prompt_dataset: Path,
    prompt_dataset_hash: str,
    created_at: str,
    openhands_version: str,
    error: str,
) -> None:
    empty_results: list[dict[str, Any]] = []
    resource_metrics = {
        "cpu_peak_percent": 0.0,
        "memory_peak_mb": 0.0,
        "avg_cpu_percent": 0.0,
        "avg_memory_mb": 0.0,
    }
    write_session_metadata(
        session_dir,
        model_name,
        created_at,
        prompt_dataset=prompt_dataset,
        prompt_dataset_hash=prompt_dataset_hash,
        openhands_version=openhands_version,
    )
    summary = summarize_prompt_results(
        model_name,
        prompt_dataset,
        session_dir,
        empty_results,
        resource_metrics,
        prompt_dataset_hash,
        openhands_version,
        created_at,
        _session_created_at(),
        error=error,
    )
    write_benchmark_results(session_dir, model_name, prompt_dataset, prompt_dataset_hash, created_at, empty_results)
    write_benchmark_summary(session_dir, summary)
    write_benchmark_report(session_dir, summary)
    write_latest_session(model_name, session_dir)


def discover_benchmark_sessions() -> list[dict[str, Any]]:
    if not BENCHMARKS_DIR.is_dir():
        return []

    sessions: list[dict[str, Any]] = []
    for session_dir in BENCHMARKS_DIR.iterdir():
        if not session_dir.is_dir() or session_dir.name == PROMPTS_DIR.name:
            continue

        summary_file = session_dir / BENCHMARK_SUMMARY_FILE
        if not summary_file.is_file():
            continue

        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(summary, dict):
            continue
        if not isinstance(summary.get("model_name"), str):
            continue
        if summary.get("workflow") != BENCHMARK_WORKFLOW:
            continue

        summary["session_dir"] = str(session_dir)
        summary["session_mtime"] = session_dir.stat().st_mtime
        sessions.append(summary)

    return sessions


def latest_session_per_model(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for session in sessions:
        model_name = session.get("model_name")
        if not isinstance(model_name, str):
            continue

        candidate = grouped.get(model_name)
        candidate_key = candidate.get("session_mtime", 0.0) if candidate else 0.0
        session_key = session.get("session_mtime", 0.0)
        if candidate is None or session_key >= candidate_key:
            grouped[model_name] = session

    return list(grouped.values())


def normalized_score(value: float, minimum: float, maximum: float, higher_is_better: bool) -> float:
    if maximum <= minimum:
        return 1.0

    ratio = (value - minimum) / (maximum - minimum)
    if higher_is_better:
        return max(0.0, min(1.0, ratio))
    return max(0.0, min(1.0, 1.0 - ratio))


def build_comparison_rows(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparable_sessions: list[dict[str, Any]] = []
    for session in sessions:
        resource_metrics = session.get("resource_metrics") or {}
        try:
            comparable_sessions.append(
                {
                    "model_name": session["model_name"],
                    "avg_quality_score": float(session.get("avg_quality_score", 0.0) or 0.0),
                    "avg_ready_seconds": float(session.get("avg_ready_seconds", 0.0) or 0.0),
                    "avg_settle_seconds": float(session.get("avg_settle_seconds", 0.0) or 0.0),
                    "success_rate": float(session.get("success_rate", 0.0) or 0.0),
                    "memory_peak_mb": float(resource_metrics.get("memory_peak_mb", 0.0) or 0.0),
                    "cpu_peak_percent": float(resource_metrics.get("cpu_peak_percent", 0.0) or 0.0),
                    "session_dir": session.get("session_dir", ""),
                    "completed_at": session.get("completed_at", ""),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue

    if not comparable_sessions:
        return []

    ready_times = [entry["avg_ready_seconds"] for entry in comparable_sessions]
    settle_times = [entry["avg_settle_seconds"] for entry in comparable_sessions]
    success_rates = [entry["success_rate"] for entry in comparable_sessions]
    quality_scores = [entry["avg_quality_score"] for entry in comparable_sessions]
    memory_peaks = [entry["memory_peak_mb"] for entry in comparable_sessions]
    cpu_peaks = [entry["cpu_peak_percent"] for entry in comparable_sessions]

    for entry in comparable_sessions:
        quality_score = normalized_score(entry["avg_quality_score"], min(quality_scores), max(quality_scores), True)
        success_score = normalized_score(entry["success_rate"], min(success_rates), max(success_rates), True)
        ready_score = normalized_score(entry["avg_ready_seconds"], min(ready_times), max(ready_times), False)
        settle_score = normalized_score(entry["avg_settle_seconds"], min(settle_times), max(settle_times), False)
        memory_score = normalized_score(entry["memory_peak_mb"], min(memory_peaks), max(memory_peaks), False)
        cpu_score = normalized_score(entry["cpu_peak_percent"], min(cpu_peaks), max(cpu_peaks), False)
        entry["score"] = round(
            (0.35 * quality_score)
            + (0.25 * success_score)
            + (0.15 * ready_score)
            + (0.10 * settle_score)
            + (0.10 * memory_score)
            + (0.05 * cpu_score),
            4,
        )

    comparable_sessions.sort(key=lambda entry: entry["score"], reverse=True)
    return comparable_sessions


def format_comparison_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Comparison Report",
        "",
        f"Generated: `{_session_created_at()}`",
        "",
        "Weights: 35% quality score, 25% success rate, 15% time to READY, 10% settle time, 10% memory, 5% CPU.",
        "",
        "| Model | Quality | Success Rate | READY | Settle | Peak RAM | Peak CPU | Score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in rows:
        lines.append(
            "| {model} | {quality} | {success} | {ready} | {settle} | {memory} | {cpu} | {score} |".format(
                model=row.get("model_name", "unknown"),
                quality=format_quality_score(row.get("avg_quality_score")),
                success=format_percent(row.get("success_rate", 0.0) * 100),
                ready=format_seconds(row.get("avg_ready_seconds")),
                settle=format_seconds(row.get("avg_settle_seconds")),
                memory=format_mb(row.get("memory_peak_mb")),
                cpu=format_percent(row.get("cpu_peak_percent")),
                score=f"{row.get('score', 0.0):.4f}",
            )
        )

    if rows:
        top_model = rows[0].get("model_name", "unknown")
        lines.extend(["", f"Best overall score: `{top_model}`."])

    return "\n".join(lines) + "\n"


def write_comparison_report(rows: list[dict[str, Any]]) -> None:
    COMPARISON_REPORT_FILE.write_text(format_comparison_report(rows), encoding="utf-8")


def write_benchmark_result_set(
    session_dir: Path,
    model_name: str,
    prompt_dataset: Path,
    prompt_dataset_hash: str,
    openhands_version: str,
    created_at: str,
    completed_at: str,
    prompt_results: list[dict[str, Any]],
    error: str | None,
) -> dict[str, Any]:
    resource_metrics = compute_resource_metrics(session_dir / "docker_stats.csv")
    summary = summarize_prompt_results(
        model_name,
        prompt_dataset,
        session_dir,
        prompt_results,
        resource_metrics,
        prompt_dataset_hash,
        openhands_version,
        created_at,
        completed_at,
        error=error,
    )
    write_benchmark_results(session_dir, model_name, prompt_dataset, prompt_dataset_hash, created_at, prompt_results)
    write_benchmark_summary(session_dir, summary)
    write_benchmark_report(session_dir, summary)
    write_latest_session(model_name, session_dir)
    return summary


def run_benchmark_command(prompt_argument: str | None) -> int:
    model_name = read_active_model_name()
    if not model_name:
        print("Error: could not determine the active OVMS model from configs/ovms_config.json", file=sys.stderr)
        return 1

    created_at = _session_created_at()
    session_dir = create_benchmark_dir(model_name, created_at=created_at)
    prompt_dataset_hash = "unknown"

    try:
        prompt_dataset = resolve_prompt_dataset_path(prompt_argument)
        prompt_dataset_hash = build_prompt_dataset_hash(prompt_dataset)
        prompts = load_prompt_dataset(prompt_dataset)
    except (FileNotFoundError, OSError, ValueError) as exc:
        warn(str(exc))
        fallback_prompt_dataset = Path(prompt_argument or DEFAULT_PROMPTS_FILE)
        write_failure_artifacts(
            session_dir,
            model_name,
            fallback_prompt_dataset,
            prompt_dataset_hash,
            created_at,
            "unknown",
            str(exc),
        )
        return 1

    client = OpenHandsClient()
    try:
        openhands_version = validate_benchmark_prerequisites(session_dir, client)
        write_session_metadata(
            session_dir,
            model_name,
            created_at,
            prompt_dataset=prompt_dataset,
            prompt_dataset_hash=prompt_dataset_hash,
            openhands_version=openhands_version,
        )
    except RuntimeError as exc:
        warn(str(exc))
        write_failure_artifacts(
            session_dir,
            model_name,
            prompt_dataset,
            prompt_dataset_hash,
            created_at,
            detect_openhands_version(),
            str(exc),
        )
        return 1

    log(f"benchmarking OpenHands against model {model_name} with {len(prompts)} prompts from {prompt_dataset}")
    stop_event, collectors = start_background_collectors(session_dir)
    prompt_results: list[dict[str, Any]] = []
    error_message: str | None = None
    return_code = 0

    try:
        for index, prompt in enumerate(prompts, start=1):
            log(f"prompt {index}/{len(prompts)}: {prompt.id} ({prompt.category})")
            result = run_openhands_prompt(client, session_dir, model_name, prompt)
            prompt_results.append(result)
            status_text = "ok" if result.get("success") else "failed"
            log(
                f"  {status_text}: quality={format_quality_score(result.get('quality_score'))}, "
                f"ready={format_seconds(result.get('ready_seconds'))}, "
                f"settle={format_seconds(result.get('settle_seconds'))}, response={result.get('response_length', 0)}"
            )
            if not result.get("success") and result.get("error"):
                warn(f"prompt {prompt.id} failed: {result['error']}")
    except KeyboardInterrupt:
        error_message = "Benchmark interrupted by user."
        return_code = 130
    except Exception as exc:
        error_message = str(exc)
        return_code = 1
    finally:
        stop_event.set()
        for collector in collectors:
            collector.join(timeout=10)

    completed_at = _session_created_at()
    write_benchmark_result_set(
        session_dir,
        model_name,
        prompt_dataset,
        prompt_dataset_hash,
        openhands_version,
        created_at,
        completed_at,
        prompt_results,
        error_message,
    )

    if error_message:
        warn(error_message)

    return return_code


def load_session_summary(session_dir: Path) -> dict[str, Any] | None:
    summary_file = session_dir / BENCHMARK_SUMMARY_FILE
    if not summary_file.is_file():
        return None

    try:
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(summary, dict):
        return None

    if not isinstance(summary.get("model_name"), str):
        return None
    if summary.get("workflow") != BENCHMARK_WORKFLOW:
        return None

    summary["session_dir"] = str(session_dir)
    summary["session_mtime"] = session_dir.stat().st_mtime
    return summary


def run_compare() -> int:
    sessions = discover_benchmark_sessions()
    if not sessions:
        print("No OpenHands benchmark sessions were found under benchmarks/.", file=sys.stderr)
        return 1

    latest_sessions = latest_session_per_model(sessions)
    rows = build_comparison_rows(latest_sessions)
    if not rows:
        print("No comparable benchmark summaries were found.", file=sys.stderr)
        return 1

    write_comparison_report(rows)
    print(f"Wrote comparison report to {COMPARISON_REPORT_FILE}")
    return 0


def run_startup() -> int:
    model_name = read_active_model_name()
    if not model_name:
        return run_compose_normally()

    tested_models = read_tested_models()
    if model_name in tested_models:
        log(f"model already benchmarked: {model_name}; running docker compose normally")
        return run_compose_normally()

    session_dir = create_benchmark_dir(model_name)
    append_tested_model(model_name)
    log(f"new model detected: {model_name}")
    log(f"collecting benchmark telemetry in {session_dir}")

    stop_event, collectors = start_background_collectors(session_dir)
    return run_compose_with_collectors(stop_event, collectors)


def print_usage() -> None:
    print(
        "Usage:\n"
        "  python3 scripts/start.py                          Start Docker Compose with first-run telemetry\n"
        "  python3 scripts/start.py benchmark [--prompts P]  Run the OpenHands benchmark against the active OVMS model\n"
        "  python3 scripts/start.py compare                  Rank completed benchmark sessions",
        file=sys.stderr,
    )


def parse_benchmark_prompt_argument(arguments: list[str]) -> str | None:
    prompt_path: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-h", "--help"}:
            print_usage()
            raise SystemExit(0)
        if argument == "--prompts":
            index += 1
            if index >= len(arguments):
                raise ValueError("--prompts requires a path")
            prompt_path = arguments[index]
        elif argument.startswith("--prompts="):
            prompt_path = argument.split("=", maxsplit=1)[1]
        else:
            raise ValueError(f"unrecognized benchmark argument: {argument}")
        index += 1
    return prompt_path


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return run_startup()

    if args in (["help"], ["--help"], ["-h"]):
        print_usage()
        return 0

    command = args[0]
    command_args = args[1:]

    if command == "benchmark":
        try:
            prompt_argument = parse_benchmark_prompt_argument(command_args)
        except SystemExit:
            return 0
        except ValueError as exc:
            print(exc, file=sys.stderr)
            print_usage()
            return 2
        return run_benchmark_command(prompt_argument)

    if command == "compare":
        if command_args:
            print_usage()
            return 2
        return run_compare()

    print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
