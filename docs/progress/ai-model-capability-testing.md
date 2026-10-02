# Progress — model capability testing

Behaviour: [011](../specifications/011-ai-model-capability-testing.md). Decision: [ADR 0015](../decisions/0015-configuration-time-model-capability-tests.md).

## Status

Complete in code. Focused tests with mocked transport and runtime regression tests pass.

## What was done

- Replaced the single "ping" with a fresh set of chat checks on every **Test Connection** click.
- Required checks, advisory warnings, and blocked follow-up checks after a base failure.
- Only a fake no-op tool is offered during the test.
- Per-check results shown on the saved `AI Model` form.
- Provider and Agno diagnostics are kept on runtime `error` events.
- Confirmed that building an agent at run time never runs the suite.

## Checks

Mocked OpenAI-compatible requests for basic, streaming, tool declaration and call, structured output, and larger input. `AI Model` integration tests cover fresh runs, warnings, blocked checks and the result format. Existing transport, builder and stream-route tests still cover their areas.

## Known environment issue

A site with an unrelated enabled default model makes the test `test_get_default_model_returns_none_when_none_set` fail. Run the tests on a clean test database.
