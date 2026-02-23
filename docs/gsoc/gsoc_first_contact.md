# First Contact — GSoC 2026

**Name:** Devesh Palo
**Project:** Demonstrating integration of OpenHands with OpenVINO Model Server
**Mentors:** Michal Kulakowski, Milosz Zeglarski
**Project Size:** 90 hours | **Difficulty:** Easy to Medium
**Date:** February 2026

---


I'm a pre-final year Electronics and Instrumentation undergrad and I've been looking at GSoC projects under the OpenVINO org. I came across the "Integration of OpenHands with OpenVINO Model Server" idea and it seemed like a good fit for what I've been learning, so I've been working on it for the past few weeks.

I'm writing before putting together a formal proposal because I want to make sure my technical direction and scope align with your expectations.

---

## What I've done so far

I built a working prototype: `github.com/devesh-047/openhands-openvino-integration`

To be clear, it's still a work in progress. The core integration is working — OVMS is running via Docker, serving TinyLlama 1.1B (INT8, CPU) through the MediaPipe `HttpLLMCalculator` pipeline, and the `/v3/chat/completions` endpoint is returning real responses. The model is converted from Hugging Face using `optimum-intel`.

What I have at this point:

- A deployment script (`deploy_ovms.sh`) that handles the container lifecycle and polls `/v1/config` until the model is in AVAILABLE state
- Validation scripts for single-turn, multi-turn, and streaming responses
- A basic latency/throughput measurement script
- Partial documentation — architecture, known limitations, compatibility notes

What I haven't done yet: I haven't hooked OpenHands up to the running endpoint and tested actual agent workflows. That's the next step, but I wanted to get some feedback before continuing.

The validation work has already turned up a few OVMS/OpenAI compatibility gaps worth noting:

| Issue | What's missing | Impact |
|-------|----------------|--------|
| G-13 | Response body has no `id`, `object`, `created`, `model` fields | Breaks strict OpenAI client parsing |
| G-02/03 | No function calling or tool use | Agent tool workflows won't work |
| G-10 | Streaming responses don't include `usage` | Token tracking incomplete |
| G-01 | Endpoint is at `/v3`, not `/v1` | Needs explicit client config |

These are in `analysis/gaps_analysis.md` in the repo. G-13 was the most surprising — the `openai` Python library (which OpenHands uses internally) requires `id` as a non-optional field, so there's a real compatibility question there.

---

## Rough plan

The project is listed as easy to medium difficulty and scoped at 90 hours. I've structured it as roughly 8 active working weeks (accounting for the shorter, non-GSoC-standard timeline for a 90-hour project):

- **Weeks 1–2**: Clean up the deployment pipeline, make model conversion reproducible, finish edge case validation
- **Weeks 3–5**: Connect OpenHands to the OVMS endpoint, test real agent tasks, see where things break
- **Weeks 6–7**: Document gaps properly with reproducible test cases; decide whether to build a thin normalization layer or just file upstream issues
- **Week 8**: Final documentation, gap summary for upstream trackers, cleanup

---

## What I'd like your advice on

A few things I'm genuinely unsure about:

**Technical direction and scope** — The prototype is promising but I've put roughly 1.5 weeks in and haven't yet tackled the hardest part: end-to-end agent testing. I'd like to confirm my direction aligns with your expectations before going further. I'm also open to discussing other OpenVINO project ideas if you feel my skills would be a better fit elsewhere.

**Model size** — TinyLlama 1.1B is fine for testing the plumbing, but it's too small to produce useful coding outputs. Should I be running the final demonstrations with a larger quantized model (Phi-2, Mistral 7B INT4) or is TinyLlama acceptable for the scope of this project?

**The G-13 issue** — OVMS dropping the top-level response fields is a real problem for OpenHands. Is there any upstream discussion about this? I can implement a normalization layer as part of the project, but I'm not sure if that's considered within scope or if the expectation is to file a bug and document the workaround.

**On the timeline** — the project is listed at 90 hours. My 8-week plan assumes roughly 10–12 hours/week. Does that framing match your expectations, or would you prefer a different breakdown? I'm flexible on the structure.

**On the expected outcome** — the project description frames the goal as a "recipe for deploying OpenHands with OVMS" plus a gaps analysis report. My current prototype repo is moving in that direction, but I want to confirm: should the final deliverable be a self-contained guide/repo that someone can follow end-to-end, or is there an existing documentation framework I should be plugging into?

---

I'm running on WSL2 Ubuntu 20.04, CPU-only, 16 GB RAM. Happy to share the repo or go through the gap analysis in more detail if that's useful.

Thanks for your time.

Devesh Palo

PS: I have compiled my ideas , doubts everything into this MD file and taken Help from an AI agent for this purpose. Rest assured the original proposal will be all my own work.
