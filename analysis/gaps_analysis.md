# Gap Analysis: OVMS as OpenAI-Compatible Backend for OpenHands

## Purpose

This document identifies functional and behavioral gaps between the OpenAI Chat Completions API (as expected by OpenHands) and the implementation provided by OpenVINO Model Server. Each gap is categorized by severity and includes a recommendation.

## Methodology

Gaps were identified through:

- Direct comparison of the OpenAI API specification (v1) against OVMS REST API behavior.
- Runtime testing with the scripts in this repository.
- Review of OpenHands source code to determine which API features the agent framework relies on.

## Gap Summary

| ID   | Feature              | OpenAI API  | OVMS Status   | Severity | Impact on OpenHands       |
|------|----------------------|-------------|---------------|----------|---------------------------|
| G-01 | API prefix           | `/v1`       | `/v3`         | Medium   | Requires explicit config  |
| G-02 | Function calling     | Supported   | Not supported | High     | Blocks structured tool use|
| G-03 | Tool use             | Supported   | Not supported | High     | Blocks structured tool use|
| G-04 | JSON mode            | Supported   | Not supported | Medium   | No guaranteed JSON output |
| G-05 | Model listing        | `/v1/models`| Not available | Low      | Manual model name config  |
| G-06 | Error schema         | Structured  | Flat string   | Low      | Client error parsing      |
| G-07 | Multiple completions | `n > 1`     | Not supported | Low      | No multi-sample strategies|
| G-08 | Log probabilities    | Supported   | Not supported | Low      | No confidence scoring     |
| G-09 | Seed parameter       | Supported   | Not supported | Low      | No reproducible outputs   |
| G-10 | Streaming `usage`    | Included    | Often absent  | Medium   | Token tracking incomplete |
| G-11 | Stop sequences       | Multiple    | Single only   | Low      | Minor prompt engineering  |
| G-12 | Token limits         | Reported    | Not reported  | Medium   | No context overflow warning|
| G-13 | Response envelope    | `id`, `object`, `created`, `model` | Absent | Medium | Clients expecting full schema fail |

## Detailed Analysis

### G-01: API Prefix Mismatch

OVMS serves at `/v3/chat/completions` rather than `/v1/chat/completions`. This is a configuration-level issue and can be addressed by setting the `base_url` to include the `/v3` prefix. However, it creates initial confusion for users migrating from OpenAI or other providers.

**Recommendation**: Document the prefix requirement prominently. Consider whether OVMS could support `/v1` as an alias in future versions.

### G-02 / G-03: Function Calling and Tool Use

This is the most significant gap. OpenHands CodeAct agent and similar agents can operate in a text-based mode, issuing code actions through natural language. However, the newer tool-use paradigm (where the model returns structured JSON specifying which tool to call and with what arguments) is not available through OVMS.

**Impact**: Agents fall back to prompt-based action parsing, which is less reliable and requires stronger model capabilities to produce consistently parseable output.

**Recommendation**: This is a feature request for OVMS. In the interim, use agents that support text-based action parsing (CodeAct in its default configuration).

### G-04: JSON Mode

Without `response_format: { type: "json_object" }`, there is no guarantee that the model output will be valid JSON even when prompted. This affects workflows where OpenHands expects structured data from the model.

**Recommendation**: Implement client-side JSON extraction with retry logic for malformed outputs.

### G-05: Model Listing

The absence of a model listing endpoint means that clients cannot programmatically discover available models. This is minor for single-model deployments but becomes inconvenient in environments with multiple served models.

**Recommendation**: Use the OVMS `/v2/models` endpoint (KServe protocol) as an alternative for model discovery, though the response format differs from the OpenAI `/v1/models` schema.

### G-06: Error Schema

OVMS returns errors as:
```json
{"error": "description string"}
```

OpenAI returns:
```json
{"error": {"message": "...", "type": "...", "param": null, "code": "..."}}
```

Clients that destructure the error object will encounter type errors.

**Recommendation**: Add client-side error normalization. This could be implemented as a thin proxy or middleware.

### G-10: Streaming Usage

Token usage statistics (`prompt_tokens`, `completion_tokens`, `total_tokens`) are not reliably included in streaming responses. OpenHands uses these for context window management and cost estimation.

**Recommendation**: Implement client-side token counting using the model's tokenizer. The `transformers` library provides the appropriate tokenizer for most Hugging Face models.

### G-12: Token Limit Reporting

OVMS does not report the model's maximum context length through the API. Clients cannot dynamically adjust their behavior based on the model's capacity.

**Recommendation**: Hardcode known context limits in the client configuration. For TinyLlama 1.1B, the limit is 2048 tokens.

### G-13: Missing Response Envelope Fields

Observed during runtime testing: OVMS responses to `/v3/chat/completions` omit the `id`, `object`, `created`, and `model` fields that the OpenAI specification requires at the top level of every response. The actual response body contains only `choices` and `usage`.

Example OVMS response:
```json
{
    "choices": [{"finish_reason": "stop", "index": 0, "message": {"content": "...", "role": "assistant"}}],
    "usage": {"completion_tokens": 28, "prompt_tokens": 8, "total_tokens": 36}
}
```

Clients that strictly parse the OpenAI schema (including those using the official `openai` Python library) will raise errors on deserialization because `id` is treated as a required field.

**Impact**: OpenHands uses the `openai` Python library internally. Depending on the client version and configuration, this may cause silent failures or explicit exceptions when processing responses.

**Recommendation**: Report this as a bug against OVMS. As a workaround, use a proxy layer that injects synthetic values for missing fields before passing responses to OpenHands.

## Risk Assessment

| Risk                                    | Likelihood | Impact  |
|-----------------------------------------|------------|---------|
| Agent failure due to missing tool use   | High       | High    |
| Context overflow on long conversations  | Medium     | Medium  |
| Misconfiguartion from API prefix        | Medium     | Low     |
| Silent quality degradation (small model)| High       | Medium  |

## Recommendations Summary

1. **Short-term**: Use text-based agent modes. Configure explicit `base_url` with `/v3`. Set conservative `max_tokens` limits.
2. **Medium-term**: Implement a lightweight API translation proxy that normalizes OVMS responses to strict OpenAI schema, adds error wrapping, and provides a `/v1/models` endpoint.
3. **Long-term**: Contribute function calling and JSON mode support to OVMS upstream, or implement these features as part of the MediaPipe graph pipeline.
