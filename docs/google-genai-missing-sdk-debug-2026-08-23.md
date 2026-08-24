# Google Provider: Missing `google-genai` SDK Debug Notes

Date: 2026-08-23
Site: `fact.local`
Component: `frappe_ai` — `AI Model.test_connection`, `lib/model.py`

## Summary

Clicking **Test Connection** on an `AI Model` linked to an `AI Provider` with slug
`google` fails with:

```
frappe.exceptions.ValidationError: `google-genai` not installed.
Please install it using `pip install google-genai`
```

The error surfaces under the title **"Missing Dependency"**. It is expected,
by-design behaviour of the current architecture (ADR 0009/0013), not a bug:
Agno executes every chat call through its **native per-provider classes**, and
the Gemini class requires Google's own SDK at import time. The fix is a one-time
`pip install`; a zero-new-dependency alternative exists via the unlinked-model
OpenAI-compatible path.

## Why litellm does not prevent this

A reasonable expectation is that having litellm installed means "no per-provider
SDKs needed". That is **not** how `frappe_ai` is designed:

- **ADR 0009** (`docs/decisions/0009-no-litellm-agno-native-models.md`) dropped
  litellm as an execution path. Chat calls execute exclusively through Agno's
  native per-provider classes (`agno.models.google.Gemini`,
  `agno.models.openai.OpenAIChat`, ...).
- **ADR 0013** (`docs/decisions/0013-litellm-for-provider-ux-agno-still-executes.md`)
  brought litellm back but scoped strictly to UX: provider-name validation and
  model-id suggestions (`AI Model.get_provider_models`). It never runs a call.
- Rationale: Agno already owns multi-provider calling via ~15+ native classes;
  routing calls through litellm underneath Agno would be a second redundant
  abstraction layer.
- Note: litellm's own `gemini/...` route also imports the Google SDK, so even a
  litellm-based design would not have avoided this install for Gemini.

## Verified Failure Chain

1. User clicks **Test Connection** on the `AI Model`.
   `frappe_ai/frappe_ai/doctype/ai_model/ai_model.py` → `test_connection()`.
   Because the model has a linked provider, it takes the linked-provider branch:
   `model_cls = get_model_class(provider_doc.provider)`.
2. `frappe_ai/lib/model.py` → `get_model_class("google")` resolves
   `PROVIDER_MODEL_CLASSES["google"] = ("agno.models.google", "Gemini")` and
   calls `importlib.import_module("agno.models.google")`.
   (`is_known_provider()` only checks the slug exists — module-existence check
   happens earlier at save time via `find_spec`, so validation passed while the
   SDK itself was absent.)
3. Importing `agno.models.google` pulls `agno/models/google/gemini.py` →
   `agno/utils/gemini.py`, which does `from google.genai.types import ...`.
4. `google-genai` is not installed in the bench env
   (`env/bin/pip show google-genai` → *Package(s) not found*; it is an optional
   Agno extra, and `frappe_ai/pyproject.toml` does not declare it).
5. Agno converts the `ModuleNotFoundError` into a friendly
   `ImportError("`google-genai` not installed...")`.
6. `test_connection()` catches it and wraps it:
   `frappe.throw(str(e), title=_("Missing Dependency"))` — the observed
   ValidationError.

This matches `get_model_class`'s own contract (docstring): *"Raises ImportError
if the provider's own SDK isn't installed — a runtime concern, distinct from
`is_known_provider`, which only checks the slug is one we support."*

## Fix Options

### Option A — Install the Google SDK (recommended)

```bash
/home/a/harsha/harsha/env/bin/pip install google-genai
```

Then restart bench (so the web workers drop any cached failed import) and retry
Test Connection.

- Pros: full native Gemini support via Agno's `Gemini` class — thinking/reasoning
  budgets, grounding, response modalities, and any other provider-specific knobs
  Agno exposes; matches ADR 0009's intended execution path.
- Cons: one extra dependency (~small; pure-API client).

### Option B — Unlinked model over Gemini's OpenAI-compatible endpoint (zero new deps)

Google officially exposes an OpenAI-wire-compatible endpoint. Configure the
`AI Model` with **no linked provider**:

| Field | Value |
|---|---|
| Provider | *(empty)* |
| Model ID | e.g. `gemini-2.5-flash` |
| Base URL | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| API Key | Gemini API key (same key works) |

Both `_model_call_config` (`api/service.py`) and `test_connection` route
unlinked models through `agno.models.openai.OpenAIChat`, which needs only the
`openai` SDK (already installed). This is the same pattern already proven live
against Groq (see `_model_call_config`'s docstring).

- Pros: no pip install; config-only; consistent with existing unlinked-model usage.
- Cons: limited to what the wire-compatible surface supports — no access to
  Gemini-native features Agno's `Gemini` class exposes (thinking configs etc.).

### Not recommended: wiring `agno.models.litellm.LiteLLM` as a provider

ADR 0009 names `agno.models.litellm.LiteLLM` as an escape hatch for providers
Agno doesn't cover natively, but it is **not** present in
`PROVIDER_MODEL_CLASSES`, so selecting it would be a code change that partially
reverses ADR 0009. Gemini *is* covered natively once its SDK is installed, so
this hatch buys nothing here. Revisit only if a genuinely uncovered provider
shows up.

## Verification

After applying Option A or B:

1. Retry **Test Connection** on the AI Model → expect `{"ok": true}` /
   "Connection OK".
2. Confirm end-to-end by firing a real run (e.g. a spec-review trigger) and
   checking the `AI Run` reaches `Completed` rather than failing with a
   provider/import error.
3. Sanity-check the other providers still resolve:
   `bench --site fact.local execute frappe_ai.lib.model.get_model_class --kwargs '{"provider": "groq"}'`.

## Related context (same session, separate issues)

Two other failure modes surfaced while debugging runs on `E-2026-0040`;
documented separately, not part of this fix:

1. `NameError` in `get_run_config` — `plugin_tools` referenced before assignment
   (`api/service.py`). Already fixed in code: added
   `plugin_tools = _resolve_agent_plugin_tools(agent_doc, user)` before the
   return dict.
2. Provider rate-limit failures ("Rate limit exceeded") currently fail the whole
   run with no retry. Agno models support built-in retries via constructor params
   (`retries`, `delay_between_retries`, `exponential_backoff` — 429s are
   classified retryable in `agno/models/base.py::_is_retryable_error`) which can
   be supplied through `AI Model.params` / `AI Provider.extra_params`. Proposed
   follow-up; see Phase 8.1 hardening notes in `docs/README.md`.
