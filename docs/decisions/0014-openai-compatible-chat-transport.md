# ADR 0014 — one OpenAI-compatible transport for chat execution

**Status:** Accepted
**Date:** 2026-08-23
**Amends:** [ADR 0009](0009-no-litellm-agno-native-models.md) and [ADR 0013](0013-litellm-for-provider-ux-agno-still-executes.md)

## Context

The previous chat path selected an Agno model class from
`PROVIDER_MODEL_CLASSES`. That made a provider's optional native SDK a runtime
dependency: selecting Google Gemini could import `google.genai`, while OpenAI-
compatible providers used a mixture of native Agno wrappers. It also duplicated
transport concerns across provider implementations.

Google, Groq, OpenRouter, and other providers expose OpenAI-compatible Chat
Completions endpoints. [Google's compatibility documentation](https://ai.google.dev/gemini-api/docs/openai)
documents the endpoint at `https://generativelanguage.googleapis.com/v1beta/openai/`,
including streaming, function calling, structured output, and provider-specific
`extra_body` fields.

## Decision

All chat execution uses one OpenAI-compatible transport backed by the OpenAI
Python SDK. Agno remains the orchestration layer for agents, tools,
confirmations, structured output handling, and streaming events.

Provider identity remains a stored configuration value. It selects endpoint
defaults and is retained in model metadata, but it never selects a native Agno
provider class or provider SDK. `PROVIDER_MODEL_CLASSES` and native provider
imports are removed from the chat path. Native provider-only features require a
future explicit adapter.

The shared resolver preserves the existing credential precedence:

- A linked `AI Provider` supplies the API key, endpoint, and shared parameters.
- An unlinked `AI Model` supplies its own API key, endpoint, and parameters.
- Model `params` override provider `extra_params`.

Known endpoint defaults include OpenAI, Google/Gemini, Groq, OpenRouter, and
other OpenAI-compatible services. An explicit provider or model `base_url`
always wins. `extra_body` is forwarded unchanged for compatibility extensions
such as Gemini's request options.

LiteLLM remains limited to provider validation and model-id suggestions. It is
not imported or called by chat execution.

The OpenAI SDK's retries are bounded to three attempts, request timeouts have a
default, and SDK/Agno failures are normalized into authentication, invalid
model, rate limit, timeout, connection, or generic provider errors. The explicit
Test Connection action runs the separate capability suite in ADR 0015 with a
shorter timeout and no retry; runtime AgentBuilder construction never calls it.

## Consequences

The runtime no longer needs `google-genai` or one native SDK per provider. The
same streaming, tool-call, structured-output, and confirmation behavior is
available across OpenAI-compatible endpoints. Provider-specific functionality
that is not represented by the compatibility API is intentionally deferred to a
future adapter rather than leaking into the common path.

The explicit `openai` dependency is now part of `frappe_ai`'s contract instead
of relying on Agno's transitive dependency.

## Alternatives Considered

### Keep Agno's native provider classes

Rejected: this requires a different optional SDK and error/feature surface for
each provider, and Google Gemini would continue to require `google-genai`.

### Execute chat directly with LiteLLM

Rejected: Agno already owns agent orchestration, tool execution, confirmations,
and streaming event handling. LiteLLM remains useful for provider/model UX but
would add a second execution layer.

### Build a separate adapter for every provider

Rejected for now: provider-specific features can be added later as explicit
adapters when a compatibility endpoint cannot represent them. The common path
should remain one transport.

## Verification

- Unit tests cover OpenAI, Google, and Groq completion requests through mocked
  OpenAI-compatible HTTP, streaming, tool calls, structured-output request
  forwarding, credential precedence, endpoint defaults, retries, and normalized
  errors.
- Route tests cover normalized provider-failure SSE plus confirmation deny and
  approve/resume behavior.
- Frappe integration tests pass for the provider transport module (14 tests),
  AI Model validation/connection module (32 tests), and focused service model
  configuration cases.
- The full app suite reaches unrelated pre-existing failures involving the
  missing `AI Agent Knowledge Base` DocType and a `FrappeClient` mock mismatch.
