# ADR 0023 — No `litellm`

**Status:** Accepted
**Date:** 2026-11-04
**Deciders:** Sri Harsha Dabbiru
**Supersedes:** [0013](0013-litellm-for-provider-ux-agno-still-executes.md)

---

## Context

`litellm` was a required dependency for two conveniences only: checking that a provider name is
"known", and suggesting model ids in the `AI Model` form. It never executed a chat or embedding
call (ADR 0014).

It also made the package impossible to install as declared:

* the pin `litellm>=1.83.0,<1.83.8` requires `openai>=2`, while the app pins `openai>=1.0,<2`;
* litellm 1.83.x pins `click` and `python-dotenv` to versions that Frappe v16 (`Click~=8.4.1`) and
  `pydantic-settings` reject, so even forcing it in breaks the bench's own requirements.

## Decision

Remove `litellm` completely.

1. **Provider names** are validated for shape only (`is_valid_provider_slug`: lowercase letters,
   digits, `.`, `_`, `-`). The app keeps no list of "known" providers: any OpenAI-compatible
   service works once it has a base URL, and a wrong endpoint or key shows up in the connection
   test. `anthropic` now has a built-in endpoint default.
2. **Model suggestions** come from the provider itself: `get_provider_models` calls the OpenAI SDK's
   `models.list()` against the provider's endpoint with its stored key (8 s timeout, no retries),
   drops embedding models, and caches the result for five minutes. Any failure returns an empty list
   and the free-text model field keeps working.
3. The `LITELLM_PROVIDER_ALIASES` table and `to_litellm_provider` are deleted.
4. Optional components are extras, not hard dependencies: `chroma` (Chroma backend) and `dev`
   (the `psycopg` driver the vector contract tests use). LanceDB stays required.

## Consequences

**Positive:** the package installs and `pip check` is clean against Frappe v16; fewer, lighter
dependencies; suggestions reflect what the provider actually offers (including private gateways and
local Ollama models) instead of a static list that ages.
**Negative:** a mistyped provider is accepted until its first use; suggestions need network access to
the provider and a saved key, and are empty for providers that do not implement `/models`.

## Alternatives considered

- *Keep litellm as an optional extra.* Still carries the conflicting pins for anyone who installs it.
- *Loosen the pin to `<1.83`.* Installs, but keeps a heavy dependency and breaks again on upgrade.
- *Move to `openai>=2`.* Does not help: litellm 1.83 then downgrades Frappe's Click.

## Verification

- `pip check` reports no broken requirements with the app installed in a Frappe v16 bench.
- The app's suite and the end-to-end scenarios pass on MariaDB and Postgres with `litellm` absent.
