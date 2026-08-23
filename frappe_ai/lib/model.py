# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Provider-independent chat transport and model configuration.

Provider identity is deliberately separate from execution. Every chat model is
created through the OpenAI-compatible Chat Completions transport, which is the
one transport Agno needs for orchestration, tools, structured output, and
streaming. The provider slug is retained as metadata and selects endpoint
defaults; it never selects an ``agno.models.<provider>`` implementation.

The only Agno model import in this module is Agno's OpenAI Chat transport. That
transport uses the OpenAI Python SDK, so providers such as Google Gemini do not
load their native SDKs (in particular, ``google.genai``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import frappe

OPENAI_COMPATIBLE_TRANSPORT = "openai_compatible"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 2
# OpenAI's ``max_retries`` is the number of retries after the initial request.
# Two retries therefore gives a maximum of three total attempts.
MAX_ALLOWED_RETRIES = 2

# Defaults are only used when the provider has not supplied an explicit endpoint.
# Providers without a standard endpoint here can still be used with a custom
# ``AI Provider.base_url`` or an unlinked model's ``base_url``.
PROVIDER_ENDPOINT_DEFAULTS: dict[str, str] = {
	"openai": "https://api.openai.com/v1",
	"google": "https://generativelanguage.googleapis.com/v1beta/openai/",
	"gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
	"groq": "https://api.groq.com/openai/v1",
	"openrouter": "https://openrouter.ai/api/v1",
	"mistral": "https://api.mistral.ai/v1",
	"deepseek": "https://api.deepseek.com/v1",
	"xai": "https://api.x.ai/v1",
	"together": "https://api.together.xyz/v1",
	"fireworks": "https://api.fireworks.ai/inference/v1",
	"perplexity": "https://api.perplexity.ai",
	"cerebras": "https://api.cerebras.ai/v1",
	"deepinfra": "https://api.deepinfra.com/v1/openai",
	"ollama": "http://localhost:11434/v1",
}

# LiteLLM uses a few different identity spellings. This translation is kept at
# the UX boundary only, for provider validation and model suggestions.
LITELLM_PROVIDER_ALIASES: dict[str, str] = {
	"google": "gemini",
	"together": "together_ai",
	"fireworks": "fireworks_ai",
	"nvidia": "nvidia_nim",
	"aws": "bedrock",
	"meta": "meta_llama",
}


class ModelConfigurationError(ValueError):
	"""Raised when an AI Model cannot be resolved into a transport config."""


@dataclass(frozen=True)
class NormalizedProviderError:
	"""Small, safe error shape shared by connection tests and run reporting."""

	code: str
	message: str
	status_code: int | None = None
	retryable: bool = False
	diagnostics: dict[str, Any] = field(default_factory=dict)

	@property
	def title(self) -> str:
		return {
			"authentication": "Authentication Failed",
			"invalid_model": "Invalid Model",
			"rate_limit": "Rate Limited",
			"timeout": "Provider Timeout",
			"connection": "Provider Unavailable",
		}.get(self.code, "Provider Error")

	def as_dict(self) -> dict[str, Any]:
		"""Return the normalized error while retaining safe provider diagnostics."""
		result: dict[str, Any] = {
			"code": self.code,
			"message": self.message,
			"status_code": self.status_code,
			"retryable": self.retryable,
		}
		if self.diagnostics:
			result["diagnostics"] = self.diagnostics
		return result


def normalize_provider(provider: str | None) -> str | None:
	"""Return a canonical provider identity without changing its meaning."""
	if not provider:
		return None
	return provider.strip().lower() or None


def normalize_transport_model_id(provider: str | None, model_id: str) -> str:
	"""Normalize provider-qualified IDs only where the transport requires it.

	The Gemini OpenAI-compatibility endpoint expects ``gemini-embedding-001``
	rather than the LiteLLM-style ``gemini/gemini-embedding-001`` identifier.
	Stored model IDs remain unchanged so provider metadata and existing records are
	preserved.
	"""
	model_id = (model_id or "").strip()
	if normalize_provider(provider) in {"gemini", "google"} and model_id.lower().startswith("gemini/"):
		return model_id.split("/", 1)[1]
	return model_id


def to_litellm_provider(provider: str) -> str:
	"""Translate an app provider identity to LiteLLM's UX spelling."""
	provider = normalize_provider(provider) or ""
	return LITELLM_PROVIDER_ALIASES.get(provider, provider)


def get_provider_endpoint(provider: str | None, explicit_base_url: str | None = None) -> str | None:
	"""Resolve an explicit endpoint first, then the provider's standard default."""
	if explicit_base_url:
		return explicit_base_url
	return PROVIDER_ENDPOINT_DEFAULTS.get(normalize_provider(provider) or "")


def is_known_provider(provider: str | None) -> bool:
	"""Whether LiteLLM recognizes the provider identity for UX purposes.

	This is intentionally not an execution registry. Any recognized provider can
	use the shared transport, with a custom endpoint where no default is known.
	"""
	provider = normalize_provider(provider)
	if not provider:
		return False
	try:
		import litellm

		return to_litellm_provider(provider) in {item.value for item in litellm.provider_list}
	except ImportError:
		return provider in PROVIDER_ENDPOINT_DEFAULTS


def _parse_object(value: Any, field_name: str) -> dict[str, Any]:
	if not value:
		return {}
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError) as exc:
			raise ModelConfigurationError(f"{field_name} must be valid JSON.") from exc
	if not isinstance(value, dict):
		raise ModelConfigurationError(f"{field_name} must be a JSON object.")
	return dict(value)


def resolve_provider_credentials(provider: str) -> dict[str, Any]:
	"""Look up shared provider credentials and endpoint settings."""
	provider = normalize_provider(provider) or ""
	if not provider or not frappe.db.exists("AI Provider", provider):
		return {}

	doc = frappe.get_doc("AI Provider", provider)
	if not doc.enabled:
		return {}

	return {
		"provider": provider,
		"api_key": doc.get_password("api_key", raise_exception=False) or None,
		"base_url": get_provider_endpoint(provider, doc.base_url or None),
		"extra_params": _parse_object(doc.extra_params, "Extra Params"),
	}


def resolve_model_config(model_doc) -> dict[str, Any]:
	"""Resolve one AI Model using the established credential precedence.

	When a provider is linked, that provider owns the API key and endpoint; model
	parameters are still applied after provider parameters so a model can tune a
	shared provider configuration. Without a provider, the model's own key,
	endpoint, and parameters are used against the shared transport.
	"""
	provider = normalize_provider(getattr(model_doc, "provider", None))
	if provider:
		provider_doc = frappe.get_doc("AI Provider", provider)
		if not provider_doc.enabled:
			raise ModelConfigurationError(f"AI Provider {provider} is disabled.")
		api_key = provider_doc.get_password("api_key", raise_exception=False) or None
		base_url = get_provider_endpoint(provider, provider_doc.base_url or None)
		provider_params = _parse_object(provider_doc.extra_params, "Extra Params")
	else:
		api_key = model_doc.get_password("api_key", raise_exception=False) or None
		base_url = model_doc.base_url or None
		provider_params = {}

	if provider and not base_url:
		raise ModelConfigurationError(
			f"Provider {provider} needs an OpenAI-compatible Base URL because no standard endpoint is configured."
		)

	model_params = _parse_object(model_doc.params, "Params")
	params = {**provider_params, **model_params}

	return {
		"provider": provider,
		"transport": OPENAI_COMPATIBLE_TRANSPORT,
		"model_id": model_doc.model_id,
		"model_type": getattr(model_doc, "model_type", None) or "Chat",
		"api_key": api_key,
		"base_url": base_url,
		"params": params,
	}


def _bounded_retries(value: Any) -> int:
	try:
		retries = int(value)
	except (TypeError, ValueError):
		retries = DEFAULT_MAX_RETRIES
	return max(0, min(retries, MAX_ALLOWED_RETRIES))


def create_openai_compatible_model(
	model_config: dict[str, Any], *, timeout: float | None = None, max_retries: int | None = None
):
	"""Create the single Agno model adapter used for every chat provider.

	The import is intentionally local and is the only Agno provider transport used
	by this app. ``params`` are forwarded to Agno's OpenAI Chat model, including
	OpenAI-compatible provider extensions such as Gemini's ``extra_body``.
	"""
	if model_config.get("model_type", "Chat") == "Embedding":
		raise ModelConfigurationError("Embedding models must use the embeddings endpoint, not chat completions.")
	try:
		from agno.models.openai.chat import OpenAIChat
	except (ImportError, ModuleNotFoundError) as exc:
		raise ModelConfigurationError("The OpenAI Python SDK is required for AI chat execution.") from exc

	params = dict(model_config.get("params") or {})
	params.setdefault(
		"role_map",
		{
			"system": "system",
			"user": "user",
			"assistant": "assistant",
			"tool": "tool",
			"model": "assistant",
		},
	)
	params.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT_SECONDS if timeout is None else timeout)
	params["max_retries"] = _bounded_retries(
		params.get("max_retries", DEFAULT_MAX_RETRIES) if max_retries is None else max_retries
	)
	# Bound both layers: the OpenAI SDK's request retry count and Agno's
	# model-level retry count. The latter defaults to zero because the SDK already
	# retries transient HTTP failures without replaying the whole orchestration turn.
	params["retries"] = _bounded_retries(params.get("retries", 0))

	kwargs: dict[str, Any] = {
		"id": normalize_transport_model_id(model_config.get("provider"), model_config["model_id"]),
		"name": "OpenAICompatibleModel",
		"provider": model_config.get("provider") or "OpenAI-compatible",
		"api_key": model_config.get("api_key"),
		"base_url": model_config.get("base_url"),
		**params,
	}

	try:
		return OpenAIChat(**kwargs)
	except (TypeError, ValueError) as exc:
		raise ModelConfigurationError(f"Invalid model parameters: {exc}") from exc


def create_openai_compatible_client(
	model_config: dict[str, Any], *, timeout: float | None = None, max_retries: int | None = None
):
	"""Create the OpenAI SDK client used by compatibility embeddings endpoints."""
	try:
		from openai import OpenAI
	except (ImportError, ModuleNotFoundError) as exc:
		raise ModelConfigurationError("The OpenAI Python SDK is required for embeddings.") from exc

	kwargs: dict[str, Any] = {
		"api_key": model_config.get("api_key"),
		"base_url": model_config.get("base_url"),
	}
	if timeout is not None:
		kwargs["timeout"] = timeout
	if max_retries is not None:
		kwargs["max_retries"] = _bounded_retries(max_retries)
	try:
		return OpenAI(**kwargs)
	except (TypeError, ValueError) as exc:
		raise ModelConfigurationError(f"Invalid model parameters: {exc}") from exc


def normalize_provider_error(
	error: BaseException | str, *, provider: str | None = None, model_id: str | None = None
) -> NormalizedProviderError:
	"""Convert SDK/Agno failures into bounded categories without losing diagnostics.

	Agno's ``RunErrorEvent`` and OpenAI-compatible SDK exceptions carry useful
	fields such as ``error_type``, ``error_id``, ``additional_data``, ``body``, and
	``request_id``. Keep those fields in a bounded, JSON-safe diagnostics object
	for SSE consumers while continuing to expose a stable normalized ``code``.
	"""
	status_code = _status_code(error)
	raw_message = _error_message(error)
	lower = raw_message.lower()

	if status_code in (401, 403) or any(
		token in lower for token in ("incorrect api key", "api key", "authentication", "unauthorized")
	):
		code = "authentication"
		retryable = False
		message = f"Authentication failed for provider '{provider or 'OpenAI-compatible'}'."
	elif status_code == 404 or any(token in lower for token in ("model not found", "does not exist", "unknown model")):
		code = "invalid_model"
		retryable = False
		message = f"Model '{model_id or 'unknown'}' was not found by provider '{provider or 'OpenAI-compatible'}'."
	elif status_code == 429 or "rate limit" in lower or "too many requests" in lower:
		code = "rate_limit"
		retryable = True
		message = f"Provider '{provider or 'OpenAI-compatible'}' rate limit exceeded."
	elif isinstance(error, TimeoutError) or any(token in lower for token in ("timed out", "timeout")):
		code = "timeout"
		retryable = True
		message = f"Provider '{provider or 'OpenAI-compatible'}' request timed out."
	elif status_code is not None and int(status_code) >= 500:
		code = "connection"
		retryable = True
		message = f"Provider '{provider or 'OpenAI-compatible'}' is temporarily unavailable."
	elif any(token in lower for token in ("connection", "connect", "dns", "network")):
		code = "connection"
		retryable = True
		message = f"Could not connect to provider '{provider or 'OpenAI-compatible'}'."
	else:
		code = "provider_error"
		retryable = False
		message = raw_message or "The provider returned an unknown error."

	return NormalizedProviderError(
		code,
		message,
		status_code=status_code,
		retryable=retryable,
		diagnostics=_error_diagnostics(error),
	)


def _error_message(error: BaseException | str) -> str:
	if isinstance(error, str):
		return error.strip()
	for field_name in ("message", "content"):
		value = getattr(error, field_name, None)
		if value:
			return str(value).strip()
	return str(error).strip()


def _status_code(error: BaseException | str) -> int | None:
	value = getattr(error, "status_code", None)
	if value is None:
		response = getattr(error, "response", None)
		value = getattr(response, "status_code", None)
	try:
		return int(value) if value is not None else None
	except (TypeError, ValueError):
		return None


def _error_diagnostics(error: BaseException | str) -> dict[str, Any]:
	if isinstance(error, str):
		return {}
	if isinstance(error, NormalizedProviderError):
		return dict(error.diagnostics)

	diagnostics: dict[str, Any] = {}
	for field_name in (
		"error_type",
		"error_id",
		"additional_data",
		"request_id",
		"body",
		"code",
		"param",
		"type",
	):
		value = getattr(error, field_name, None)
		if value is not None:
			diagnostics[field_name] = _json_safe(value)

	response = getattr(error, "response", None)
	if response is not None:
		response_request_id = getattr(response, "request_id", None)
		if response_request_id is not None and "request_id" not in diagnostics:
			diagnostics["request_id"] = _json_safe(response_request_id)
		response_body = getattr(response, "text", None)
		if response_body and "body" not in diagnostics:
			diagnostics["body"] = _json_safe(response_body)

	return diagnostics


def _json_safe(value: Any) -> Any:
	"""Bound arbitrary SDK metadata before it crosses the SSE boundary."""
	if isinstance(value, str):
		return value[:4000]
	if value is None or isinstance(value, (bool, int, float)):
		return value
	if isinstance(value, dict):
		return {str(key): _json_safe(item) for key, item in list(value.items())[:50]}
	if isinstance(value, (list, tuple)):
		return [_json_safe(item) for item in value[:50]]
	return str(value)[:4000]


def get_default_model() -> str | None:
	"""Return the name of the enabled AI Model marked is_default=1, or None."""
	return frappe.db.get_value("AI Model", {"is_default": 1, "enabled": 1}, "name")
