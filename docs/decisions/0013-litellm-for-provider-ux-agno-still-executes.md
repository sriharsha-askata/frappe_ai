# ADR 0013 — litellm reintroduced for provider/model UX; Agno still executes every call

**Status:** Accepted
**Date:** 2026-08-08
**Deciders:** Sri Harsha Dabbiru
**Amends:** [ADR 0009](0009-no-litellm-agno-native-models.md) (does not reverse it — see
below)
**Prompted by:** `AI Model.model_id` had zero autocomplete suggestions and
`AI Model.provider` had drifted to an `Autocomplete` bare-string field (an
undocumented follow-up fix, see `docs/learnings.md` "Agno needs a real provider SDK
per provider"), both closing off model selection as a usable, friendly workflow.

---

## Context

ADR 0009 dropped `litellm` entirely: chat calls go through Agno's native
per-provider classes (`agno.models.<slug>.<Class>`), and `AI Provider`/`AI Model`
validate provider slugs against a small curated dict,
`PROVIDER_MODEL_CLASSES` (`frappe_ai/lib/model.py`). That decision is correct and
unchanged — Agno remains the only thing that ever executes a chat call.

Two follow-on problems this ADR closes:

1. **No model-id suggestions.** `AI Model.get_provider_models()` was a hardcoded
   no-op returning `[]`, because nothing replaced litellm's
   `models_by_provider` registry after ADR 0009. The `model_id` field's
   autocomplete was always empty — the user had to already know the exact
   model id string.
2. **`AI Model.provider` had drifted away from `Link → AI Provider`.** An
   undocumented follow-up (`docs/learnings.md`) changed it to `Autocomplete`
   so a model didn't need a matching `AI Provider` document just to save —
   Frappe validates a `Link` field's target-row existence at save time,
   independent of `reqd`. This traded away standard Link UX (quick-entry,
   "View"/"Create New", list-view search) for that flexibility.

The user decided to reverse both, mirroring the predecessor `flow` app's
architecture (`apps/flow/flow/flow/doctype/flow_model/`, `flow_provider/`), which
uses `litellm.provider_list`/`litellm.models_by_provider` for exactly this UX, with
`Flow Model.provider` as a plain enforced `Link`.

---

## Decision

### litellm is a dependency again, scoped to UX/validation only

`pyproject.toml` declares `litellm` (same pin as `apps/flow/pyproject.toml`).
It is used in exactly two places, both non-execution:

- `AI Provider._validate_provider_known` — validates `self.provider` against
  `litellm.provider_list` instead of `PROVIDER_MODEL_CLASSES`. This is a broader
  set than Agno's curated 21 slugs (litellm recognizes far more providers than
  Agno ships native classes for).
- `AI Model.get_provider_models(provider)` — returns
  `sorted(litellm.models_by_provider[provider])`, two-factor filtered: litellm
  must know the provider's model list, **and** `is_known_provider(provider)`
  (Agno) must be true, or `[]` is returned. This is a deliberate divergence
  from `flow`'s own unfiltered `get_provider_models` — `flow` has no separate
  "can this app actually run this provider" concept, since litellm executes
  everything there; `frappe_ai` does have that concept (Agno's class map), so
  suggesting a model id for a provider Agno can't call would be a UX trap.

`_model_call_config`/`AIModel.test_connection()` — the actual chat-execution
paths — never import or reference litellm. They still resolve an Agno class via
`get_model_class`/`PROVIDER_MODEL_CLASSES` exclusively.

This directly revisits ADR 0009's own "Alternatives Considered" rejection of "keep
litellm for provider/model-id validation... use Agno only for orchestration" (its
lines 116–121), which called this a two-layer design risking "two sources of
truth for what provider is this." The risk is accepted now because it's
contained: litellm never decides what gets called, only what's suggested or
accepted as a well-formed provider name. The two registries (litellm's provider
list, Agno's `PROVIDER_MODEL_CLASSES`) can diverge in *breadth* (litellm knows
more providers) without ever diverging in *behavior* — Agno's map is still the
sole authority on what a linked provider actually resolves to at call time.

### Provider-slug spelling mismatch — `LITELLM_PROVIDER_ALIASES`

litellm and Agno spell six providers differently for the same underlying
service:

| Agno / `PROVIDER_MODEL_CLASSES` slug | litellm slug |
|---|---|
| `google` | `gemini` |
| `together` | `together_ai` |
| `fireworks` | `fireworks_ai` |
| `nvidia` | `nvidia_nim` |
| `aws` | `bedrock` |
| `meta` | `meta_llama` |

`frappe_ai/lib/model.py` adds `LITELLM_PROVIDER_ALIASES` (Agno slug → litellm
slug) and `to_litellm_provider()`. `AI Provider._validate_provider_known` and
`get_provider_models` both translate through this map before touching litellm's
registries — the **stored** `AI Provider.provider` value stays in Agno's
spelling (what `get_model_class`/`PROVIDER_MODEL_CLASSES` need), only the
litellm-facing lookups are translated. Discovered by a real test failure
(`test_model.py`'s `fireworks` fixture) during implementation, not anticipated
up front — confirms the mismatch is a real, narrow gap rather than a
hypothetical one.

### `AI Model.provider` is a real `Link → AI Provider` again

`ai_model.json`: `provider` is `fieldtype: "Link"`, `options: "AI Provider"`,
`reqd` stays unset (non-mandatory, unchanged). `AIModel._validate_provider_known`
is removed entirely — Frappe core's own `_validate_links()` now enforces that a
non-empty `provider` names a real `AI Provider` row (`frappe.LinkValidationError`
if not), the same standard mechanism every other Link field in this app uses.
No auto-create-on-save or bypass machinery — if you type a provider, the row
must already exist, exactly like `Flow Model.provider`.

### Credential resolution: a hard two-state split, not a merge

This is the one place this ADR changes runtime *behavior*, not just validation/UX.
Previously (`_model_call_config`, pre-ADR-0013), a linked `AI Provider`'s
credentials were the *base*, with the `AI Model`'s own `api_key`/`base_url`
*overriding* them when set (`resolve_provider_credentials` + per-model
override merge). That precedence existed to serve the Autocomplete-`provider`
design's flexibility (a `provider` slug alone, no `AI Provider` doc required,
model carries its own credentials).

With `provider` now an enforced Link, that flexibility moves to the *unlinked*
state instead, and the precedence becomes a clean either/or:

- **`AI Model.provider` set (linked):** `base_url`, `api_key`, and the Agno
  class all come from the linked `AI Provider` document alone. The `AI
  Model`'s own `api_key`/`base_url` fields are not read in this state — no
  per-model override when linked.
- **`AI Model.provider` empty (unlinked):** `base_url`, `api_key` come from
  the `AI Model`'s own fields, and the Agno class defaults to
  `agno.models.openai.OpenAIChat` — the same "any OpenAI-wire-compatible
  endpoint via `base_url`" pattern already proven live against Groq in Phase
  3, now automatic for an unlinked model instead of requiring
  `provider="openai"` to be typed in.

`_model_call_config`'s previous hard "No Provider" throw is removed — an
unlinked model is now a fully runnable state, not an error.

`resolve_provider_credentials` (`frappe_ai/lib/model.py`) was also used by the
then-current `frappe_ai/knowledge/embedder.py` (ADR 0012, Phase 4 embeddings).
That embedding configuration is superseded by [ADR 0016](0016-fixed-ollama-embeddings.md)
and remains independent of the chat path.

---

## Consequences

### Positive

- `AI Model.model_id` now gets real, live autocomplete suggestions.
- `AI Model.provider` regains standard Link-field UX (quick-entry search,
  "View"/"Create New" from the form, list-view filtering by provider).
- Credential resolution is simpler to reason about: two states, no merge order
  to remember.

### Negative / Behavior changes

- **Any existing `AI Model` that relied on a per-model `api_key`/`base_url`
  override winning over a linked provider's credentials will now silently use
  the provider's credentials instead** — the override is gone, not merely
  reprioritized. Anyone with this configuration must move the credential onto
  the `AI Provider` record or unlink the model.
- **A model must have (or gain) a real `AI Provider` document to use a
  linked provider** — the "no `AI Provider` document required" flexibility
  from the Autocomplete era is gone for the *linked* state; it's preserved
  only in the *unlinked* state (own `api_key`/`base_url`, `OpenAIChat` class).
- Six provider slugs need `LITELLM_PROVIDER_ALIASES` translation to work with
  litellm's registries — a small, fixed maintenance surface if litellm renames
  a provider or Agno adds a new slug that also happens to disagree with
  litellm's spelling.

### Neutral

- `PROVIDER_MODEL_CLASSES`/`is_known_provider`/`get_model_class` are unchanged
  in role — still the sole source of truth for which Agno class a linked
  provider's slug maps to.
- Context-window auto-detection (`litellm.get_model_info`) remains out of
  scope, per ADR 0009's original "Negative" consequence — this ADR does not
  reopen it. `context_window` stays a plain user-editable `Int`.

---

## Alternatives Considered

### Auto-create the `AI Provider` row on save if none exists for the typed slug
Considered first, since it would have preserved "no `AI Provider` document
required" even in the linked state. Rejected: it reintroduces exactly the kind
of implicit, silently-created state this codebase's other ADRs (e.g. ADR
0011) have consistently avoided, and raises unresolved questions (what
`api_key` does the auto-created row get?) that a plain enforced Link avoids by
construction. The unlinked-model fallback path covers the "no `AI Provider`
document needed" use case instead, with an explicit user choice (link or
don't) rather than implicit row creation.

### Leave `get_provider_models` unfiltered, matching `flow` exactly
Rejected per explicit instruction — `frappe_ai` (unlike `flow`) has a real
"can Agno actually run this provider" question that `flow` never needed to
ask, since `flow` executes everything through litellm itself. Suggesting a
model id for a provider Agno has no class for would be worse UX than no
suggestion at all.

### Keep the provider-then-model credential merge, just extend it to the unlinked case
Rejected — a merge with a fallback-to-unlinked-defaults branch is more states
to reason about than a clean two-way split, for a benefit (per-model override
while linked) nobody asked for and the original design used only to route
around the Link-existence requirement this ADR removes anyway.

---

## Verification

- `AI Provider(provider="not-a-real-litellm-provider")` raises
  `frappe.ValidationError` ("Unknown provider"); a real litellm provider name
  (including the six aliased ones) saves.
- `AI Model(provider="not-a-real-AI-Provider-doc")` raises
  `frappe.LinkValidationError`; a real `AI Provider` docname saves.
- `get_provider_models("openai")` returns a non-empty sorted list;
  `get_provider_models("fireworks")` returns a non-empty list via the alias;
  a provider litellm can't map to any Agno class returns `[]`.
- `_model_call_config`: a linked model's own `api_key`/`base_url` are ignored
  in favor of the provider's; an unlinked model uses its own fields against
  `OpenAIChat`; an unlinked model with no credentials at all still resolves
  (no throw).
- Full suite: same pre-existing 7 `ignore_user_permissions`/`test_conditions`
  failures (documented, unrelated to this change), no new failures.

---

## References

- [ADR 0009 — No litellm, Agno native models](0009-no-litellm-agno-native-models.md)
  (amended by this ADR, not reversed — chat execution stays Agno-only)
- [ADR 0016 — Fixed Ollama embeddings](0016-fixed-ollama-embeddings.md)
  (current embedding configuration; independent of chat provider UX)
- `docs/learnings.md` — "Agno needs a real provider SDK per provider" (the
  undocumented Link→Autocomplete follow-up this ADR formally supersedes)
- [003 — DocType Reference](../specifications/003-doctype-reference.md) §1–2
- `apps/flow/flow/flow/doctype/flow_model/`, `flow_provider/` — the
  predecessor implementation this ADR's UX mirrors
