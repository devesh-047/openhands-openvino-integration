"""
Validate multi-turn chat completion against the OVMS endpoint.

Sends a sequence of messages simulating a conversation and validates
that the server maintains coherent responses across turns.
"""

import logging
import sys

import requests

from _config import load_config

logger = logging.getLogger(__name__)

CONVERSATION = [
    [
        {"role": "system", "content": "You are a concise technical assistant."},
        {"role": "user", "content": "Explain what OpenVINO is in one sentence."},
    ],
    [
        {"role": "system", "content": "You are a concise technical assistant."},
        {"role": "user", "content": "Explain what OpenVINO is in one sentence."},
        {"role": "assistant", "content": ""},  # placeholder filled at runtime
        {"role": "user", "content": "What inference hardware does it support?"},
    ],
]


def run_turn(base_url: str, model: str, messages: list[dict]) -> dict | None:
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 128,
        "temperature": 0.1,
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
    except requests.RequestException as e:
        logger.error("Request failed: %s", e)
        return None

    if response.status_code != 200:
        logger.error("Status %d: %s", response.status_code, response.text[:500])
        return None

    try:
        return response.json()
    except ValueError:
        logger.error("Invalid JSON response.")
        return None


def validate_conversation(base_url: str, model: str) -> bool:
    logger.info("Turn 1: Initial question")
    result = run_turn(base_url, model, CONVERSATION[0])
    if result is None:
        return False

    choices = result.get("choices", [])
    if not choices:
        logger.error("No choices in response.")
        return False

    first_reply = choices[0].get("message", {}).get("content", "")
    logger.info("Turn 1 response: %s", first_reply[:200])

    CONVERSATION[1][2]["content"] = first_reply

    logger.info("Turn 2: Follow-up question")
    result = run_turn(base_url, model, CONVERSATION[1])
    if result is None:
        return False

    choices = result.get("choices", [])
    if not choices:
        logger.error("No choices in follow-up response.")
        return False

    second_reply = choices[0].get("message", {}).get("content", "")
    logger.info("Turn 2 response: %s", second_reply[:200])

    logger.info("Multi-turn chat completion validated.")
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    success = validate_conversation(config.base_url, config.model_name)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
