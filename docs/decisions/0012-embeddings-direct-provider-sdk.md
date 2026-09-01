# ADR 0012 — Embeddings via direct provider SDK calls, not Agno

**Status:** Superseded by [ADR 0016](0016-fixed-ollama-embeddings.md)
**Date:** 2026-08-06

## Context

Phase 4 needs `frappe_ai.knowledge.embedder.embed_texts()` / `probe_dimension()`, ported
from `flow.knowledge.embedder`. `flow`'s implementation has exactly one provider call site:

```python
response = litellm.embedding(input=texts, timeout=timeout, **config)
```

litellm's `embedding()` is provider-agnostic — one function, routed by a `model` string
prefix, working against any of litellm's ~100 supported providers without needing that
provider's own SDK installed.

`frappe_ai` does not use litellm (ADR 0009) — chat calls go through Agno's native
per-provider classes (`agno.models.<slug>.<Class>`, `frappe_ai/lib/model.py`). The natural
question is whether embeddings should follow the same path. They cannot: this bench's
installed `agno==2.8.7` has no `agno.embedder` package at all (confirmed via
`importlib.util.find_spec`). Agno's chat model classes (`OpenAIChat`, `Claude`, `Groq`, …)
expose `.response()`/`.aresponse()` for chat completions; none expose an `.embed()` method.
Embeddings are simply outside what Agno's model classes do.

## Decision

Call each provider's own embeddings endpoint directly via that provider's official SDK,
keyed off `AI Model.provider` — the same slug already used for chat, but resolved through a
**separate**, smaller mapping (`EMBEDDING_CALLERS` in `frappe_ai/knowledge/embedder.py`)
containing only providers with a real embeddings API, not all 21 chat slugs.

`AI Settings.embedding_model` is `Link → AI Model` (not a raw model-id string, unlike
`flow.Flow Knowledge Settings.embedding_model`) — this was already true before Phase 4
touched anything. So `embed_texts`/`probe_dimension` load the `AI Model` doc, take its
`provider` + `model_id`, and resolve credentials as `model.api_key or base_url` first, then
`resolve_provider_credentials(provider)` (`frappe_ai/lib/model.py`) as fallback — the exact
precedence Phase 3's chat path already uses, so a model can carry its own embedding
credentials or share an `AI Provider` row, consistent with how chat models already work.

Phase 4 ships one caller: `openai` (the only embeddings-capable SDK installed in this
bench, confirmed via `find_spec` — `anthropic`, `groq`, `cohere`, `google.genai`,
`mistralai` are all absent). This is not a hard limitation: any OpenAI-wire-compatible
embeddings endpoint (Groq does not currently serve embeddings, but many self-hosted /
proxy setups do) works today via `AI Model(provider="openai", base_url=<endpoint>)`, the
same pattern Phase 3's learnings established for chat. Adding a second caller (e.g.
`cohere`) is a new entry in `EMBEDDING_CALLERS` plus that provider's SDK — additive, no
change to the resolution logic.

## Consequences

**Positive**

- No embeddings-shaped hole in ADR 0009's "no litellm" decision — the same reasoning
  (avoid a redundant abstraction layer, avoid requiring N SDKs for N providers a user might
  never configure) applies, and is honoured by keeping `EMBEDDING_CALLERS` opt-in per
  provider rather than eagerly supporting all 21 chat slugs.
- Order-preservation, batching-at-96, and the vector-count mismatch guard from `flow`'s
  `_embed_batch` port unchanged — none of that logic is litellm-specific.
- `probe_dimension`/`embed_texts` share `AI Model`'s existing credential-resolution
  precedence with chat, rather than inventing a second scheme.

**Negative**

- Fewer embedding providers work out of the box than `flow` had via litellm (effectively
  all of litellm's ~100 vs. `frappe_ai`'s one, initially). Mitigated: adding a provider is
  additive and small; the common case (any OpenAI-wire-compatible endpoint) already works
  via `base_url` override with zero new SDKs, matching how `frappe_ai` already handles
  Groq-for-chat.
- Two mappings now exist (`PROVIDER_MODEL_CLASSES` for chat, `EMBEDDING_CALLERS` for
  embeddings) instead of one. Accepted — they genuinely serve different capabilities
  (chat completion vs. embeddings), and conflating them would mean a provider entry silently
  implying a capability (embeddings) the chat mapping never promised.

## Alternatives Considered

| Approach | Verdict |
|---|---|
| Route embeddings through Agno anyway | Not possible — no `agno.embedder` in the installed version; would require vendoring or a version bump with no confirmed embeddings support either |
| Reintroduce litellm for embeddings only | Rejected — reopens exactly the redundant-abstraction-layer question ADR 0009 already settled, for one call site |
| One `EMBEDDING_CALLERS` entry per `PROVIDER_MODEL_CLASSES` slug (21 providers) up front | Rejected — most of those providers' SDKs aren't installed and may not even offer embeddings (e.g. `xai`, `deepseek` have no public embeddings API); building 20 unused/unverifiable callers is speculative work with no way to test most of them in this environment |
| **Direct provider SDK calls, opt-in mapping, `openai` shipped first** | **Chosen** |

## Verification

- `probe_dimension("<AI Model name>")` returns the correct vector width against a real
  `AI Model(provider="openai", ...)`.
- `embed_texts` preserves input order under a mocked out-of-order provider response
  (`entry.index`-based re-sort, ported from `flow`).
- A provider slug with no `EMBEDDING_CALLERS` entry fails with a clear error naming the
  provider, not a stack trace.
- Unit tests patch the SDK call site directly (`openai.OpenAI(...).embeddings.create`),
  mirroring how `flow`'s tests patched `litellm.embedding`.
