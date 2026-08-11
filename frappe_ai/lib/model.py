# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Provider credential resolution and Agno model-class lookup.

Chat calls still go through Agno's native per-provider classes only, never litellm
(ADR 0009). litellm is used elsewhere in `frappe_ai` (`AI Provider` name validation,
`AI Model.get_provider_models` suggestions) purely for provider/model-id UX — see
ADR 0013 — never to execute a call. This module owns only the config-tier concerns:
resolving `AI Provider` credentials for a provider slug, and mapping a slug to
its `agno.models.<slug>` class. Actual chat/streaming belongs to Agno's `Agent`
and model classes (Phase 3), not here.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import frappe

# Fixed set of Agno-supported provider slugs this app validates against, each mapped to
# its `agno.models.<module>` import path and primary model class. Checked by module
# existence only (importlib.util.find_spec) — never imported at validation time — so
# save-time validation doesn't depend on which optional per-provider SDKs happen to be
# installed. The provider's own SDK (e.g. `anthropic`, `google-genai`) is only required
# when a model is actually instantiated, e.g. in test_connection().
PROVIDER_MODEL_CLASSES: dict[str, tuple[str, str]] = {
	"openai": ("agno.models.openai", "OpenAIChat"),
	"anthropic": ("agno.models.anthropic", "Claude"),
	"ollama": ("agno.models.ollama", "Ollama"),
	"openrouter": ("agno.models.openrouter", "OpenRouter"),
	"azure": ("agno.models.azure", "AzureOpenAI"),
	"google": ("agno.models.google", "Gemini"),
	"gemini": ("agno.models.google", "Gemini"),
	"groq": ("agno.models.groq", "Groq"),
	"cohere": ("agno.models.cohere", "Cohere"),
	"mistral": ("agno.models.mistral", "MistralChat"),
	"xai": ("agno.models.xai", "xAI"),
	"together": ("agno.models.together", "Together"),
	"fireworks": ("agno.models.fireworks", "Fireworks"),
	"deepseek": ("agno.models.deepseek", "DeepSeek"),
	"perplexity": ("agno.models.perplexity", "Perplexity"),
	"sambanova": ("agno.models.sambanova", "Sambanova"),
	"nvidia": ("agno.models.nvidia", "Nvidia"),
	"huggingface": ("agno.models.huggingface", "HuggingFace"),
	"deepinfra": ("agno.models.deepinfra", "DeepInfra"),
	"aws": ("agno.models.aws", "AwsBedrock"),
	"cerebras": ("agno.models.cerebras", "Cerebras"),
	"meta": ("agno.models.meta", "Llama"),
}

# Agno's slug -> litellm's slug for the providers where the two disagree (litellm's
# `provider_list`/`models_by_provider` use a different spelling for the same
# provider). Used to translate an Agno slug into what litellm's registries key on,
# for `AI Provider` name validation and `get_provider_models` suggestions (ADR 0013)
# — litellm itself is never involved in resolving which Agno class actually runs a
# call (`get_model_class`, `PROVIDER_MODEL_CLASSES`), only in these two UX-only spots.
LITELLM_PROVIDER_ALIASES: dict[str, str] = {
	"google": "gemini",
	"together": "together_ai",
	"fireworks": "fireworks_ai",
	"nvidia": "nvidia_nim",
	"aws": "bedrock",
	"meta": "meta_llama",
}


def to_litellm_provider(provider: str) -> str:
	"""Translate an Agno/`PROVIDER_MODEL_CLASSES` slug into litellm's spelling for the
	same provider, or return it unchanged if the two already agree."""
	return LITELLM_PROVIDER_ALIASES.get(provider, provider)


def is_known_provider(provider: str) -> bool:
	"""Whether `provider` is a slug this app maps to an Agno model class."""
	return provider in PROVIDER_MODEL_CLASSES


def get_model_class(provider: str):
	"""Import and return the Agno model class for `provider`. Raises ImportError if the
	provider's own SDK isn't installed — that's a runtime concern, distinct from
	`is_known_provider`, which only checks the slug is one we support."""
	module_path, class_name = PROVIDER_MODEL_CLASSES[provider]
	module = importlib.import_module(module_path)
	return getattr(module, class_name)


def get_default_model() -> str | None:
	"""Return the name of the enabled AI Model marked is_default=1, or None."""
	return frappe.db.get_value("AI Model", {"is_default": 1, "enabled": 1}, "name")


def resolve_provider_credentials(provider: str) -> dict[str, Any]:
	"""Look up central AI Provider credentials for a provider slug."""
	if not provider or not frappe.db.exists("AI Provider", provider):
		return {}

	doc = frappe.get_doc("AI Provider", provider)
	if not doc.enabled:
		return {}

	return {
		"api_key": doc.get_password("api_key", raise_exception=False) or None,
		"base_url": doc.base_url or None,
		"extra_params": json.loads(doc.extra_params) if doc.extra_params else {},
	}
