# ADR 0009 — Drop litellm; use Agno's native per-provider model classes

**Status:** Accepted — **amended in part by
[ADR 0013](0013-litellm-for-provider-ux-agno-still-executes.md)** (2026-08-08):
litellm is a dependency again, but scoped strictly to provider-name validation
and model-id suggestions (`AI Provider`, `AI Model.get_provider_models`). The
core decision below — chat calls execute exclusively through Agno's native
per-provider classes, never litellm — is unchanged and still fully in force.
The "Verification" section's "no litellm dependency" bullet is superseded;
see ADR 0013 for what replaced it.
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Context

`flow` — the functional specification for this app — calls `litellm.completion()`
directly for every model call, and uses `litellm.provider_list`,
`litellm.get_llm_provider()`, and `litellm.get_model_info()` to validate
providers, compose `provider/model` id strings, and auto-detect context windows
(`Flow Provider`, `Flow Model`, `flow/lib/model.py`). The original `002-feature-mapping.md`
and `003-doctype-reference.md` planned a mechanical **Port** of this design onto
`AI Provider` / `AI Model`, and `001-architecture.md` §10 listed `litellm` among
dependencies "already present" and implicitly reused.

Phase 1 implementation surfaced the conflict this created: `frappe_ai` is already
committed to Agno for orchestration ([ADR 0001](0001-agno-fastapi-over-frappe-native.md)),
and Agno owns model calling itself via ~15+ native per-provider classes
(`agno.models.openai.OpenAI`, `agno.models.anthropic.Anthropic`,
`agno.models.ollama.Ollama`, `agno.models.openrouter.OpenRouter`, etc., each
accepting its own `id`, `api_key`, `base_url`). Routing every call through
litellm underneath Agno would be a second, redundant provider-abstraction layer
— `flow` needed litellm because it had no other way to call multiple providers
uniformly; `frappe_ai` does not have that problem, because Agno already solves it.

Agno does still ship `agno.models.litellm.LiteLLM` as one adapter among the
others — litellm is not incompatible with Agno, just unnecessary as the default
path when Agno's own classes cover the providers this app needs.

---

## Decision

`frappe_ai` does not depend on `litellm`. `AI Provider` and `AI Model` are
redesigned around Agno's native model classes instead of litellm's provider
strings:

- `AI Provider.provider` is validated against a fixed set of Agno-supported
  provider slugs (`openai`, `anthropic`, `ollama`, `openrouter`, `azure`,
  `gemini`, `groq`, ...), each mapped 1:1 to an `agno.models.<slug>` import
  path — not against `litellm.provider_list`.
- `AI Model.model_id` is a **bare** model id (e.g. `"claude-sonnet-4-6"`), passed
  as the resolved Agno class's `id=` param. It is no longer a composite
  `provider/model` string parsed by litellm; the provider comes from the linked
  `AI Provider` (or `AI Model.provider`), not from the id string itself.
- `context_window` auto-detection (`litellm.get_model_info()`) is **dropped**.
  Agno exposes no equivalent registry. The field becomes a plain user-editable
  `Int`, not `read_only`/derived.
- `test_connection()` instantiates the resolved Agno model class and issues its
  own minimal call, instead of one generic `litellm.completion(max_tokens=1)`.
  This makes the ping provider-class-specific rather than uniform, and depends
  on `agno` being installed — deferred to whichever phase first declares that
  dependency (see Consequences).
- `frappe_ai/lib/model.py` keeps only credential resolution
  (`resolve_provider_credentials`) for building the kwargs an Agno model class
  needs. It does **not** port `flow/lib/model.py`'s `Model.chat()` /
  `ChatResponse` / streaming machinery — that responsibility belongs entirely to
  Agno's `Agent` and model classes in Phase 3, and re-implementing it against
  litellm here would just be more code to delete later.
- An escape hatch remains: if a provider genuinely isn't covered by an Agno
  native class, `agno.models.litellm.LiteLLM` can be selected per-model like any
  other Agno class. This is a per-model choice available to whoever configures
  `AI Provider`, not a dependency this app declares or relies on by default.

---

## Consequences

### Positive

- One fewer dependency, one fewer abstraction layer between `AI Model` config
  and the actual provider call.
- `AI Model` configuration maps directly onto what Agno will do with it in
  Phase 3 — no translation step from a litellm string to an Agno class at
  `AgentBuilder.build()` time.
- Removes a class of bugs `flow` was exposed to: litellm's provider inference
  from a string (`litellm.get_llm_provider()`) occasionally disagreeing with
  what the user intended; Agno's explicit provider-to-class mapping has no
  equivalent ambiguity.

### Negative

- **Loses automatic context-window detection.** `flow` auto-filled
  `context_window` from `litellm.get_model_info()`; `frappe_ai` requires the
  user to enter it (or leave it 0/unknown). A real but minor regression in
  convenience — nothing downstream in the current spec depends on
  `context_window` being auto-derived.
- **`test_connection()` is provider-class-specific**, not one generic code path.
  Slightly more surface area to maintain as new provider slugs are added
  (mitigated by there being a small, curated set rather than "whatever litellm
  supports").
- **A provider not covered by any Agno native class** requires either waiting
  on Agno to add one, or explicitly configuring that model with
  `agno.models.litellm.LiteLLM` (which does reintroduce litellm, just scoped to
  that one model rather than as an app-wide dependency).

### Neutral

- `AI Provider`/`AI Model`'s DocType shape (fields, naming, permissions) is
  unchanged from what `003-doctype-reference.md` already specified — only the
  validation logic and `context_window`'s `read_only` attribute change.
- This does not affect ADR 0001's earlier rejection of "FastAPI without Agno
  (direct litellm)" — that alternative was about replacing Agno's
  *orchestration* with hand-rolled litellm calls, a different question from
  whether litellm sits underneath Agno for model calls. This ADR only answers
  the latter.

---

## Alternatives Considered

### Keep litellm for provider/model-id validation and context-window detection, use Agno only for orchestration
Rejected. This is the two-layer design described in Context — `AI Model.model_id`
would need parsing by litellm to validate, then re-resolving into an Agno class
to actually run. Two sources of truth for "what provider is this" invites drift
between them, for a benefit (auto context-window, one broad validation call)
that is minor.

### Use `agno.models.litellm.LiteLLM` as the default/only Agno model class app-wide
Rejected as the default. This keeps litellm as a hard dependency and as much
indirection as the original design, just moved one layer down — Agno's own
native classes are simpler and are what Agno's docs present as the standard
path. Left available as a per-model escape hatch, not the default.

### Build a small internal provider registry independent of both litellm and Agno
Rejected. Would duplicate a mapping Agno already maintains (provider slug →
model class), for no benefit — `frappe_ai` would own a list that has to be kept
in sync with Agno's own supported-provider list regardless.

---

## Verification

- `AI Provider.provider` rejects a slug with no corresponding `agno.models.<slug>`
  module.
- `AI Model.model_id` accepts a bare model id with no `provider/` prefix
  requirement.
- `pyproject.toml` declares no `litellm` dependency; `frappe_ai/lib/model.py`
  contains no `import litellm`.
- `test_connection()` succeeds against at least one real provider once `agno`
  is installed (Phase 2 or later, per whichever phase first declares it).

---

## References

- [001 — Architecture](../specifications/001-architecture.md)
- [002 — Feature Mapping](../specifications/002-feature-mapping.md) §1
- [003 — DocType Reference](../specifications/003-doctype-reference.md) §1–2
- [ADR 0001 — Agno + FastAPI over Frappe-native](0001-agno-fastapi-over-frappe-native.md)
- Agno model provider index: `docs.agno.com/models/providers/model-index` (live reference, not vendored)
