"""
Test server-sent event (SSE) streaming from the OVMS chat completions endpoint.

Sends a streaming request and validates that the response delivers
incremental chunks conforming to the OpenAI streaming protocol.
"""

import json
import logging
import sys
import time

import requests

from _config import load_config

logger = logging.getLogger(__name__)


def parse_sse_line(line: str) -> dict | None:
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    data_str = line[len("data:"):].strip()
    if data_str == "[DONE]":
        return None
    try:
        return json.loads(data_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse SSE data: %s", data_str[:200])
        return None


def test_streaming(base_url: str, model: str) -> bool:
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Count from 1 to 5."}
        ],
        "max_tokens": 64,
        "temperature": 0.1,
        "stream": True,
    }

    logger.info("Sending streaming request to %s", url)

    try:
        response = requests.post(url, json=payload, timeout=120, stream=True)
    except requests.ConnectionError:
        logger.error("Connection refused. Is OVMS running at %s?", base_url)
        return False
    except requests.Timeout:
        logger.error("Streaming request timed out.")
        return False

    if response.status_code != 200:
        logger.error("Status %d: %s", response.status_code, response.text[:500])
        return False

    chunk_count = 0
    collected_content = []
    start_time = time.monotonic()

    for line in response.iter_lines(decode_unicode=True):
        parsed = parse_sse_line(line)
        if parsed is None:
            continue

        chunk_count += 1
        choices = parsed.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                collected_content.append(content)

        if chunk_count == 1:
            first_token_time = time.monotonic() - start_time
            logger.info("Time to first token: %.3f seconds", first_token_time)

    elapsed = time.monotonic() - start_time
    full_content = "".join(collected_content)

    logger.info("Received %d chunks in %.3f seconds", chunk_count, elapsed)
    logger.info("Assembled content: %s", full_content[:200])

    if chunk_count == 0:
        logger.error("No streaming chunks received.")
        return False

    logger.info("Streaming validation passed.")
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    success = test_streaming(config.base_url, config.model_name)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
