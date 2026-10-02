# ADR 0015 — Model capabilities are tested on request, not on every run

**Status:** Accepted · **Date:** 2026-08-23

## The problem

A single "ping" proved only that one request could reach the provider. Chat also depends on streaming, function calling and sometimes structured output, which some endpoints handle badly. Checking these before *every* run would add delay and duplicate provider calls.

## The decision

Testing is **explicit and happens at configuration time**. The **Test Connection** button on a saved `AI Model` runs a fresh suite of chat checks each time it is clicked. The checks reuse the same OpenAI-compatible client as real chat ([ADR 0014](0014-openai-compatible-chat-transport.md)) and offer the model only a fake no-op tool, never a real one.

- **Strict (required):** the basic chat request, streaming, and the tool declaration / call / result round trip.
- **Warnings (advisory):** structured JSON output and a larger input, because support and limits vary.
- A failure in setup or authentication **blocks** the dependent checks so you do not see misleading follow-up failures.
- The result is returned to the form and not stored, so it never goes stale.
- Normal runs never call it and are not blocked by its result.

Details of the checks and result format: [011](../specifications/011-ai-model-capability-testing.md).

## What follows

**Good:** operators get useful capability information when they set up a model, with no cost on later runs.

**Limits:** a passing result does not guarantee production success. Outages, quota changes, retired models and later larger prompts remain run-time issues, and the suite cannot test real business tools because it deliberately never runs them.

## Related

[011](../specifications/011-ai-model-capability-testing.md), [ADR 0014](0014-openai-compatible-chat-transport.md), [ADR 0016](0016-fixed-ollama-embeddings.md).
