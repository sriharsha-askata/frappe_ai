# Progress — AI Model Capability Testing

## Overall status

Complete in code; focused mocked transport and runtime regression tests pass.

## Completed

- Replaced the single Test Connection ping with a fresh Chat/Embedding capability suite.
- Added strict core checks, advisory warnings, and blocked dependent checks.
- Restricted tool testing to a synthetic no-op function.
- Added per-check UI rendering for saved AI Model forms.
- Preserved Agno/provider diagnostics on runtime SSE error events.
- Verified runtime AgentBuilder construction does not invoke the configuration suite.

## Verification

- Mocked OpenAI-compatible Chat suite: basic, streaming, tool declaration/call,
  structured output, and bounded input request shapes.
- Mocked Embedding suite: single input, batch input, dimensions, counts, and Gemini ID normalization.
- AI Model integration tests cover fresh invocations, warnings, blocked failures, and result contracts.
- Existing transport, builder, and SSE route tests remain covered.

## Known environment issue

The local `tact.local` database contains an unrelated enabled default AI Model,
so the pre-existing `test_get_default_model_returns_none_when_none_set` fixture
fails outside a clean test database.
