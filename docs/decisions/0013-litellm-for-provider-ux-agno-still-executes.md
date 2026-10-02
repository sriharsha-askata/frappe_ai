# ADR 0013 — litellm only suggests providers and models; it never makes a call

**Status:** Accepted · **Date:** 2026-08-08 · **Updated:** 2026-08-23 (the call itself is now made by one OpenAI-compatible client, [ADR 0014](0014-openai-compatible-chat-transport.md))

## The problem

Two things made model setup awkward:

1. **No suggestions.** The `model_id` box on `AI Model` had nothing to suggest, so a user had to know the exact model id string.
2. **`AI Model.provider` had become a free-text field** so a model could be saved without a matching `AI Provider` record. That lost Frappe's normal link behaviour (search, "View", "Create New", list filters).

## The decision

### litellm is a dependency again, for suggestions and validation only

`pyproject.toml` declares `litellm`. It is used in two places and **never to make a call**:

- `AI Provider` validation: the provider name must be one litellm recognizes (`is_known_provider` in `lib/model.py`).
- `get_provider_models(provider)` on `AI Model`: returns litellm's known model ids for that provider as autocomplete suggestions (embedding-style ids are filtered out). Typing any other model id by hand is still allowed.

All real calls go through the shared OpenAI-compatible client ([ADR 0014](0014-openai-compatible-chat-transport.md)). litellm cannot change what gets called, only what is suggested or accepted as a well-formed provider name. If litellm is not installed, validation falls back to a built-in list of known endpoints.

### Provider names that spell differently

litellm and this app spell a few providers differently. `LITELLM_PROVIDER_ALIASES` (in `lib/model.py`) translates the app's name to litellm's, only when talking to litellm. The stored value stays the app's spelling.

| Stored in the app | litellm |
|---|---|
| `google` | `gemini` |
| `together` | `together_ai` |
| `fireworks` | `fireworks_ai` |
| `nvidia` | `nvidia_nim` |
| `aws` | `bedrock` |
| `meta` | `meta_llama` |

### `AI Model.provider` is a real link to `AI Provider` again

If you set a provider, that `AI Provider` record must exist (Frappe's own link check enforces it). It is optional: leaving it empty is allowed.

### Connection details: two states, no merging

| `AI Model.provider` | Where the key and URL come from |
|---|---|
| **Set** | The linked `AI Provider` only. The model's own `api_key` and `base_url` are ignored |
| **Empty** | The model's own `api_key` and `base_url`, against any OpenAI-compatible endpoint |

A model with no provider and no credentials is still a valid saved record, not an error. When a provider has no explicit `base_url`, the app uses a default for the common providers (`PROVIDER_ENDPOINT_DEFAULTS` in `lib/model.py`, for example OpenAI, Google, Groq, OpenRouter, Mistral, DeepSeek, Ollama).

## What follows

**Good**
- Real autocomplete for model ids.
- Standard link behaviour on `provider`.
- A model's connection details are easy to reason about: either the provider's or its own.

**Costs and changes**
- A model that relied on its own `api_key`/`base_url` overriding a linked provider's now uses the provider's. Move the credential onto the provider or unlink the model.
- Using a provider requires an `AI Provider` record. For a one-off endpoint, leave the provider empty and fill the model's own fields.
- The alias table is a small fixed maintenance point if litellm renames something.
- litellm is a heavy package used only for suggestions (a lighter static list would also work; see [010](../specifications/010-review-topics.md)).

Context-window detection stays out: `context_window` is a number you type.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Create the `AI Provider` record automatically on save | Silent hidden state, and it is unclear what key such a record would hold. The "provider empty" state covers the use case explicitly |
| Suggest model ids for every provider litellm knows | Superseded: the suggestion list is no longer tied to which providers the app can run, because every provider uses the same OpenAI-compatible client |
| Keep the provider-then-model credential merge | More states to reason about, for a feature nobody needed once the link is enforced |

## How we check

- An unknown provider name on `AI Provider` is rejected; a real one (including the aliased ones) saves.
- An `AI Model` pointing at a missing `AI Provider` is rejected by the link check.
- `get_provider_models("openai")` returns a sorted, non-empty list; an unknown provider returns an empty list.
- A linked model ignores its own key and URL; an unlinked model uses them; an unlinked model without credentials still resolves.

Tests: `test_ai_provider.py`, `test_ai_model.py`, `frappe_ai/tests/test_model.py`.

## Related

[ADR 0014](0014-openai-compatible-chat-transport.md), [003 AI Provider and AI Model](../specifications/003-doctype-reference.md#models), [Learnings](../learnings.md).
