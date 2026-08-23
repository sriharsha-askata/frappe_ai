# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Embedding calls for the knowledge store, via direct provider SDKs.

Adapted from `flow.knowledge.embedder` (`apps/flow/flow/knowledge/embedder.py`), which
called `litellm.embedding()` — a single provider-agnostic entry point. `frappe_ai` does
not use litellm (ADR 0009) and Agno's model classes expose chat completions only, no
embeddings call (confirmed: no `agno.embedder` package in the installed version) — see
[ADR 0012](../../docs/decisions/0012-embeddings-direct-provider-sdk.md) for the full
reasoning. Each provider's own SDK is called directly instead, keyed off `AI Model.provider`
through `EMBEDDING_CALLERS`, a small registry separate from the chat transport's
provider endpoint defaults, containing only providers with a real embeddings API
and an installed SDK.

Credentials resolve provider-then-model (`frappe_ai.lib.model.resolve_provider_credentials`
as the base, `AI Model`'s own `api_key`/`base_url` as an override) — this is now
embeddings-specific; chat calls (`api/service.py`'s `_model_call_config`) moved to a
stricter two-state split per ADR 0013 (linked Provider XOR model's own fields, no merge).

`AI Settings.embedding_model` is `Link → AI Model` (unlike `flow`'s raw model-id string),
so `probe_dimension`/`_embedding_config` take an `AI Model` document name.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from frappe_ai.lib.model import normalize_transport_model_id, resolve_provider_credentials

BATCH_SIZE = 96  # smallest common provider batch limit (Cohere)
EMBED_TIMEOUT = 120
PROBE_TIMEOUT = 30


def embed_texts(texts: list[str]) -> list[list[float]]:
	"""Embed texts with the model from AI Settings, preserving input order."""
	if not texts:
		return []
	config = _embedding_config()
	vectors: list[list[float]] = []
	for start in range(0, len(texts), BATCH_SIZE):
		vectors.extend(_embed_batch(texts[start : start + BATCH_SIZE], config, timeout=EMBED_TIMEOUT))
	return vectors


def probe_dimension(model: str) -> int:
	"""Vector width an AI Model's embeddings come back with, via a one-input call."""
	config = _model_config(model)
	(vector,) = _embed_batch(["dimension probe"], config, timeout=PROBE_TIMEOUT)
	return len(vector)


def _embedding_config() -> dict[str, Any]:
	settings = frappe.get_cached_doc("AI Settings")
	if not settings.embedding_model:
		frappe.throw(
			_("Set an embedding model in AI Settings first."),
			title=_("Knowledge Not Configured"),
		)
	return _model_config(settings.embedding_model)


def _model_config(model: str) -> dict[str, Any]:
	doc = frappe.get_doc("AI Model", model)
	if not doc.enabled:
		frappe.throw(_("AI Model {0} is disabled.").format(model), title=_("Model Disabled"))
	if (doc.get("model_type") or "Chat") != "Embedding":
		frappe.throw(
			_("AI Model {0} is a Chat model. Select Model Type = Embedding for knowledge indexing.").format(model),
			title=_("Invalid Embedding Model"),
		)
	if not doc.provider:
		frappe.throw(
			_("AI Model {0} has no Provider slug set.").format(model),
			title=_("No Provider"),
		)

	provider_creds = resolve_provider_credentials(doc.provider)
	return {
		"provider": doc.provider,
		"model": normalize_transport_model_id(doc.provider, doc.model_id),
		"api_key": provider_creds.get("api_key") or doc.get_password("api_key", raise_exception=False) or "",
		"base_url": provider_creds.get("base_url") or doc.base_url,
	}


def _embed_batch(texts: list[str], config: dict[str, Any], *, timeout: int) -> list[list[float]]:
	caller_name = EMBEDDING_CALLERS.get(config["provider"])
	if caller_name is None:
		frappe.throw(
			_("Provider {0} does not support embeddings, or its SDK isn't wired up yet.").format(
				config["provider"]
			),
			title=_("Embedding Failed"),
		)
	# Resolved by name at call time (not stored as a direct function reference) so
	# tests can patch the module-level caller function.
	caller = globals()[caller_name]

	try:
		entries = caller(texts, config, timeout=timeout)
	except Exception as e:
		frappe.throw(str(e)[:500] or type(e).__name__, title=_("Embedding Failed"))

	entries = sorted(entries, key=lambda entry: entry[0])
	vectors = [[float(value) for value in vector] for _, vector in entries]
	if len(vectors) != len(texts):
		frappe.throw(
			_("Embedding provider returned {0} vectors for {1} inputs.").format(len(vectors), len(texts)),
			title=_("Embedding Failed"),
		)
	return vectors


def _call_openai_compatible(
	texts: list[str], config: dict[str, Any], *, timeout: int
) -> list[tuple[int, list[float]]]:
	"""Any OpenAI-wire-compatible embeddings endpoint — the real OpenAI API, or another
	provider's OpenAI-compatible proxy reached via `base_url` (same pattern Phase 3's
	chat path already uses for Groq; see `docs/learnings.md`)."""
	from openai import OpenAI

	client = OpenAI(api_key=config["api_key"] or None, base_url=config["base_url"] or None, timeout=timeout)
	response = client.embeddings.create(model=config["model"], input=texts)
	return [(entry.index, entry.embedding) for entry in response.data]


# Providers with a real embeddings API and a call site here. Opt-in and small on
# purpose — see ADR 0012. Adding a provider is additive: a new entry plus that
# provider's SDK, no change to the resolution logic above. Values are function
# *names* (resolved via `globals()` in `_embed_batch`), not references, so a test
# patching e.g. `frappe_ai.knowledge.embedder._call_openai_compatible` is honoured.
EMBEDDING_CALLERS: dict[str, str] = {
	"openai": "_call_openai_compatible",
	"gemini": "_call_openai_compatible",
	"google": "_call_openai_compatible",
}
