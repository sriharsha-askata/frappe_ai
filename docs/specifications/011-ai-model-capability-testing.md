# AI Model Capability Testing

`AI Model.test_connection` is an explicit, configuration-time action on a saved
AI Model. Saving a model does not call an external provider, and runtime agent
execution does not call this method or perform a preflight probe.

Every invocation creates a fresh suite and returns:

```json
{
  "ok": true,
  "checks": [{"name": "chat", "status": "passed"}],
  "warnings": []
}
```

Each check includes `name`, `status`, `required`, and a useful `message`. Failed
checks also include a normalized `code` and bounded provider diagnostics when
available. Statuses are `passed`, `failed`, `warning`, or `blocked`.

Chat models use the same Agno OpenAI-compatible transport as runtime execution:

- non-streaming completion;
- streaming completion;
- synthetic tool declaration;
- synthetic tool-call plus result and follow-up completion;
- structured JSON output; and
- a bounded larger-input request.

Only the local synthetic no-op tool is supplied during testing. Frappe, Tender,
database, MCP, and business tools are never dispatched.

All `AI Model` rows are chat models. Embeddings use the separate fixed Ollama
integration described in [ADR 0016](../decisions/0016-fixed-ollama-embeddings.md),
so the model capability suite never tests or selects an embedding model.

Chat endpoint checks are required for `ok`. Structured output and
larger-input checks are advisory and produce warnings on failure. If transport
configuration, authentication, or the base request fails, dependent checks are
blocked so the result does not report misleading secondary failures.
