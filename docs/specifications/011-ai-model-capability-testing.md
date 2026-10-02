# 011 — Testing what a model can do

The **Test Connection** button on an `AI Model` form (the whitelisted method `AIModel.test_connection`, needs write permission) checks that the saved model actually works with the way the app uses it. It exists so that problems such as "this endpoint ignores tools" show up when you configure the model, not in the middle of a user's chat.

It only runs when someone clicks the button. Saving a model does not call the provider, and normal agent runs never run these checks first.

## What it returns

A fresh set of checks on every click:

```json
{
  "ok": true,
  "checks": [ { "name": "chat", "status": "passed", "required": true, "message": "…" } ],
  "warnings": []
}
```

Each check has `name`, `status`, `required` and `message`. A failed check also has a normalized error `code` and a bounded amount of provider detail.

Statuses:

| Status | Meaning |
|---|---|
| `passed` | Worked |
| `failed` | A required check failed; `ok` is false |
| `warning` | An optional check failed; `ok` can still be true |
| `blocked` | Skipped because an earlier required check failed (so you do not get misleading follow-up errors) |

## What is checked

The checks use the same OpenAI-compatible client as real chat ([ADR 0014](../decisions/0014-openai-compatible-chat-transport.md)):

- a normal (non-streaming) reply — **required**
- a streaming reply
- a tool declaration the model can see
- a tool call, a tool result sent back, and a follow-up reply
- structured JSON output — advisory
- a larger input — advisory

Only a built-in fake no-op tool is offered during the test. Frappe data, business tools and MCP tools are never called.

## What is not tested

Every `AI Model` is a chat model. Embeddings use a single fixed Ollama model configured separately ([ADR 0016](../decisions/0016-fixed-ollama-embeddings.md)), so this test never selects or tests an embedding model.

Code: `frappe_ai/frappe_ai/doctype/ai_model/connection_test.py`, with the button in `ai_model.js`. Decision: [ADR 0015](../decisions/0015-configuration-time-model-capability-tests.md).
