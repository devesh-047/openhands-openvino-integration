"""
Validate that the OVMS endpoint responds to basic OpenAI-compatible API requests.

Tests the /v3/chat/completions endpoint with a minimal payload and verifies
response structure conforms to the OpenAI chat completion schema.
"""

import logging
import sys

import requests
from pydantic import BaseModel, ValidationError

from _config import load_config

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str
    content: str


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    # id, object, created, and model are required by the OpenAI spec but OVMS
    # does not include them in responses. Marked optional to allow validation
    # of the fields that are present; gaps are reported as warnings below.
    id: str | None = None
    object: str | None = None
    created: int | None = None
    model: str | None = None
    choices: list[Choice]
    usage: Usage | None = None


def test_endpoint(base_url: str, model: str) -> bool:
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "What is the capital of France?"}
        ],
        "max_tokens": 64,
        "temperature": 0.1,
    }

    logger.info("Sending request to %s", url)

    try:
        response = requests.post(url, json=payload, timeout=120)
    except requests.ConnectionError:
        logger.error("Connection refused. Is OVMS running at %s?", base_url)
        return False
    except requests.Timeout:
        logger.error("Request timed out after 120 seconds.")
        return False

    if response.status_code != 200:
        logger.error(
            "Unexpected status %d: %s", response.status_code, response.text[:500]
        )
        return False

    try:
        data = response.json()
    except ValueError:
        logger.error("Response is not valid JSON: %s", response.text[:500])
        return False

    try:
        parsed = ChatCompletionResponse(**data)
    except ValidationError as e:
        logger.error("Response schema validation failed:\n%s", e)
        return False

    logger.info("Response validated successfully.")

    missing_fields = [f for f in ("id", "object", "created", "model") if getattr(parsed, f) is None]
    if missing_fields:
        logger.warning(
            "OVMS response omits OpenAI required fields: %s (see gaps_analysis.md G-01)",
            ", ".join(missing_fields),
        )

    if not parsed.choices:
        logger.error("Response contains no choices.")
        return False

    logger.info("Finish reason: %s", parsed.choices[0].finish_reason)
    logger.info("Content: %s", parsed.choices[0].message.content[:200])

    if parsed.usage:
        logger.info(
            "Token usage: prompt=%d completion=%d total=%d",
            parsed.usage.prompt_tokens,
            parsed.usage.completion_tokens,
            parsed.usage.total_tokens,
        )

    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    success = test_endpoint(config.base_url, config.model_name)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
