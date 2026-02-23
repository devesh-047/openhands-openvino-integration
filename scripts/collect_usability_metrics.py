"""
Collect usability metrics from the OVMS endpoint for integration analysis.

Measures response latency, token throughput, and error rates across
repeated requests. Results are logged and written to a JSON report.
"""

import json
import logging
import statistics
import sys
import time
from pathlib import Path

import requests

from _config import load_config

logger = logging.getLogger(__name__)

NUM_ITERATIONS = 10
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "analysis"

PROMPTS = [
    "Write a Python function that reverses a string.",
    "Explain the difference between a list and a tuple in Python.",
    "What is dependency injection?",
    "How does TCP three-way handshake work?",
    "Write a bash one-liner to find all .py files in a directory.",
]


def measure_request(base_url: str, model: str, prompt: str) -> dict:
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 128,
        "temperature": 0.1,
    }

    start = time.monotonic()
    try:
        response = requests.post(url, json=payload, timeout=180)
        elapsed = time.monotonic() - start
    except requests.RequestException as e:
        elapsed = time.monotonic() - start
        return {
            "prompt": prompt[:80],
            "status": "error",
            "error": str(e),
            "latency_seconds": round(elapsed, 4),
        }

    result = {
        "prompt": prompt[:80],
        "status_code": response.status_code,
        "latency_seconds": round(elapsed, 4),
    }

    if response.status_code == 200:
        try:
            data = response.json()
            usage = data.get("usage", {})
            result["prompt_tokens"] = usage.get("prompt_tokens", 0)
            result["completion_tokens"] = usage.get("completion_tokens", 0)
            result["total_tokens"] = usage.get("total_tokens", 0)
            content = data["choices"][0]["message"]["content"]
            result["response_length"] = len(content)
            result["status"] = "success"
        except (KeyError, IndexError, ValueError):
            result["status"] = "parse_error"
    else:
        result["status"] = "http_error"

    return result


def run_collection(base_url: str, model: str) -> list[dict]:
    results = []
    for i in range(NUM_ITERATIONS):
        prompt = PROMPTS[i % len(PROMPTS)]
        logger.info("Iteration %d/%d: %s", i + 1, NUM_ITERATIONS, prompt[:60])
        result = measure_request(base_url, model, prompt)
        results.append(result)
        logger.info(
            "  Status: %s | Latency: %.3fs",
            result["status"],
            result["latency_seconds"],
        )
    return results


def compute_summary(results: list[dict]) -> dict:
    successful = [r for r in results if r["status"] == "success"]
    latencies = [r["latency_seconds"] for r in successful]

    summary = {
        "total_requests": len(results),
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "error_rate": round((len(results) - len(successful)) / len(results), 4),
    }

    if latencies:
        summary["latency"] = {
            "min": round(min(latencies), 4),
            "max": round(max(latencies), 4),
            "mean": round(statistics.mean(latencies), 4),
            "median": round(statistics.median(latencies), 4),
            "stdev": round(statistics.stdev(latencies), 4) if len(latencies) > 1 else 0,
        }

    completion_tokens = [r.get("completion_tokens", 0) for r in successful]
    if completion_tokens and latencies:
        total_tokens = sum(completion_tokens)
        total_time = sum(latencies)
        summary["throughput_tokens_per_second"] = round(total_tokens / total_time, 2)

    return summary


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()

    logger.info("Starting usability metrics collection against %s", config.base_url)
    results = run_collection(config.base_url, config.model_name)
    summary = compute_summary(results)

    logger.info("Summary: %s", json.dumps(summary, indent=2))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / "metrics_report.json"
    report = {"config": {"base_url": config.base_url, "model": config.model_name}, "summary": summary, "results": results}
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
