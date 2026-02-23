# OpenAI API Compatibility Notes

## Overview

OVMS implements a subset of the OpenAI Chat Completions API. This document details the supported and unsupported features as observed during integration testing.

## Endpoint

OVMS exposes the chat completions endpoint at:

```
POST /v3/chat/completions
```

Note the `/v3` prefix. This differs from the standard OpenAI `/v1` prefix. Clients must be configured accordingly.

## Supported Request Fields

| Field            | Supported | Notes                                              |
|------------------|-----------|---------------------------------------------------|
| `model`          | Yes       | Must match the model name in `ovms_config.json`   |
| `messages`       | Yes       | System, user, and assistant roles                  |
| `max_tokens`     | Yes       | Controls maximum generation length                 |
| `temperature`    | Yes       | Values 0.0-2.0                                     |
| `top_p`          | Yes       | Nucleus sampling parameter                         |
| `stream`         | Partial   | SSE streaming; see limitations below               |
| `stop`           | Partial   | Single stop sequence supported                     |
| `n`              | No        | Only `n=1` is supported                            |
| `functions`      | No        | Function calling is not implemented                |
| `tools`          | No        | Tool use is not implemented                        |
| `response_format`| No        | JSON mode is not available                         |
| `logprobs`       | No        | Log probabilities are not returned                 |
| `seed`           | No        | Deterministic generation is not supported          |

## Response Schema

The response follows the OpenAI schema:

```json
{
    "id": "cmpl-...",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "tiny-llama-1.1b-chat",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "..."
            },
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "prompt_tokens": 25,
        "completion_tokens": 50,
        "total_tokens": 75
    }
}
```

## Streaming Behavior

When `stream: true` is set, OVMS returns server-sent events (SSE):

```
data: {"id":"cmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"token"},"finish_reason":null}]}

data: [DONE]
```

Known streaming behaviors:

- The first chunk may include a `delta` with `role: "assistant"` and no content.
- `finish_reason` is `null` on intermediate chunks and set on the final chunk.
- The `usage` field may be absent from streamed responses depending on OVMS version.
- The `[DONE]` sentinel terminates the stream.

## Error Responses

OVMS returns errors in a simplified format compared to the OpenAI standard:

```json
{
    "error": "Model not found: nonexistent-model"
}
```

The OpenAI standard wraps errors in an `error` object with `message`, `type`, `param`, and `code` fields. Clients that parse error responses strictly may need adaptation.

## Token Counting

- Tokenization is performed by the model's tokenizer, not the OpenAI tokenizer (tiktoken).
- Token counts in `usage` reflect the model's actual tokenization, which may differ from what OpenAI-based clients expect.
- For context window management, the model's actual token limit should be used rather than values from OpenAI model metadata.

## Model Name Handling

- OVMS requires the `model` field to exactly match the configured model name.
- OpenHands sends the model name configured in its settings. This must be set to the exact string used in `ovms_config.json`.
- There is no model aliasing or fallback behavior.
