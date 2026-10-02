# ADR 0014 — One OpenAI-compatible client for every chat model

**Status:** Accepted · **Date:** 2026-08-23

## The problem

Chat calls used to go through a native Agno class per provider (an OpenAI class, a Gemini class, and so on). Each needed its own optional SDK, so choosing Google Gemini imported `google.genai` and failed if that package was missing. Providers also differed in errors and features, and the same transport concerns were repeated per provider.

Most providers (Google, Groq, OpenRouter, Mistral, DeepSeek, Ollama and others) offer an **OpenAI-compatible** chat endpoint that supports streaming, function calling and structured output.

## The decision

**All chat goes through one OpenAI-compatible client, the OpenAI Python SDK**, used through Agno's OpenAI chat model (`create_openai_compatible_model` in `lib/model.py`). Agno still runs the agent loop: tools, confirmations, structured-output handling and streaming events.

- The provider name is stored and kept in model metadata. It selects **default endpoints** (for example OpenAI, Google, Groq, OpenRouter) but never selects a provider-specific class or SDK.
- An explicit `base_url` on the provider or model always wins.
- Connection details come from the linked `AI Provider`, or from the unlinked `AI Model` itself ([ADR 0013](0013-litellm-for-provider-ux-agno-still-executes.md)). Model `params` override provider `extra_params`. `extra_body` is passed through unchanged for provider extensions (for example Gemini's request options).
- **Retries and timeouts are bounded:** at most two retries after the first attempt (three tries in total), and every request has a timeout (default 600 s for long streaming tool calls).
- **Errors are normalized** (`normalize_provider_error`) into authentication, invalid model, rate limit, timeout, connection, or generic provider errors, with a `retryable` flag, so the UI gets a consistent message.
- litellm is only used for provider validation and suggestions.
- Features that a provider offers outside the compatible API need an explicit adapter later; they are not mixed into the common path.
- The explicit **Test Connection** checks ([ADR 0015](0015-configuration-time-model-capability-tests.md)) use a shorter timeout and no retry. Building an agent never runs them.

## What follows

**Good**
- No `google-genai` and no per-provider SDK at run time. The `openai` package is now an explicit dependency (`pyproject.toml`) instead of arriving through Agno.
- Streaming, tool calls, structured output and confirmations behave the same across providers.

**Costs**
- Provider-specific features that the compatible API cannot express are not available until someone writes an adapter.
- Quality of a "compatible" endpoint varies, which is why Test Connection exists.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Keep Agno's native provider classes | Different optional SDK, errors and features per provider; Gemini needs `google-genai` |
| Call models directly through litellm | Agno already owns orchestration; litellm would be a second execution layer |
| One adapter per provider now | Not needed yet; add one only when a feature cannot be represented |

## How we check

Tests cover requests to OpenAI, Google and Groq style endpoints through mocked HTTP, streaming, tool calls, structured-output forwarding, credential precedence, default endpoints, retries and normalized errors, plus route tests for failure events and approve/deny/resume.

Tests: `frappe_ai/tests/test_openai_transport.py`, `test_model.py`, `test_chat_route.py`.

## Related

[ADR 0013](0013-litellm-for-provider-ux-agno-still-executes.md), [ADR 0015](0015-configuration-time-model-capability-tests.md), [003 AI Model](../specifications/003-doctype-reference.md#models).
