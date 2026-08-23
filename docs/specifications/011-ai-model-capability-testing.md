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

Embedding models use the OpenAI-compatible embeddings endpoint for one input and
a small batch, then verify response counts and vector dimensions. Gemini model
IDs are normalized only at the transport boundary.

Chat and embedding endpoint checks are required for `ok`. Structured output and
larger-input checks are advisory and produce warnings on failure. If transport
configuration, authentication, or the base request fails, dependent checks are
blocked so the result does not report misleading secondary failures.
