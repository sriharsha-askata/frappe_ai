# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Configuration-time capability checks for an ``AI Model``.

This module deliberately sits outside the runtime agent builder.  It is called
only by the saved AI Model form's explicit ``Test Connection`` action and uses
the same OpenAI-compatible transport as runtime chat execution.  The only tool
ever supplied to a model during a check is the synthetic no-op below.
"""

from __future__ import annotations

import json
from typing import Any

from agno.models.message import Message
from agno.tools.function import Function

from frappe_ai.lib.model import (
	NormalizedProviderError,
	create_openai_compatible_client,
	create_openai_compatible_model,
	normalize_provider_error,
	normalize_transport_model_id,
)

CHAT_CHECKS = (
	"chat",
	"streaming",
	"tool_declaration",
	"tool_call",
	"structured_output",
	"large_input",
)
EMBEDDING_CHECKS = ("embedding_single", "embedding_batch", "embedding_dimensions")
CORE_CHAT_CHECKS = frozenset({"chat", "streaming", "tool_declaration", "tool_call"})
CORE_EMBEDDING_CHECKS = frozenset(EMBEDDING_CHECKS)
OPTIONAL_CHAT_CHECKS = frozenset({"structured_output", "large_input"})

# Keep the larger-input probe useful while ensuring an accidental click cannot
# send an unbounded payload to a provider.  This is a character bound, not a
# promise about a model's token window.
LARGE_INPUT_CHARS = 16_384
EMBEDDING_BATCH_INPUTS = ("frappe ai embedding probe one", "frappe ai embedding probe two")
SYNTHETIC_TOOL_NAME = "frappe_ai_connection_test_noop"


def run_capability_suite(model_config: dict[str, Any]) -> dict[str, Any]:
	"""Run a fresh capability suite for one resolved model configuration.

	The caller resolves configuration before entering this function.  A factory
	failure is still handled here so every provider failure gets the same result
	contract and dependent checks are marked ``blocked`` rather than attempted.
	"""
	if model_config.get("model_type", "Chat") == "Embedding":
		return _run_embedding_suite(model_config)
	return _run_chat_suite(model_config)


def blocked_suite(
	check_names: tuple[str, ...],
	error: BaseException | str,
	*,
	provider: str | None = None,
	model_id: str | None = None,
) -> dict[str, Any]:
	"""Build a result for a configuration failure before a transport exists."""
	normalized = normalize_provider_error(error, provider=provider, model_id=model_id)
	checks = [
		_failure(check_names[0], normalized, required=check_names[0] not in OPTIONAL_CHAT_CHECKS),
	]
	checks.extend(
		_blocked(name, normalized.message, required=name not in OPTIONAL_CHAT_CHECKS)
		for name in check_names[1:]
	)
	return _suite_result(checks)


def _run_chat_suite(model_config: dict[str, Any]) -> dict[str, Any]:
	try:
		model = create_openai_compatible_model(model_config, timeout=15, max_retries=0)
	except Exception as error:
		return blocked_suite(
			CHAT_CHECKS,
			error,
			provider=model_config.get("provider"),
			model_id=model_config.get("model_id"),
		)

	checks: list[dict[str, Any]] = []
	try:
		response = model.response(messages=[Message(role="user", content="Reply with the word OK.")])
		if _response_content(response) is None:
			raise ValueError("The provider returned an empty chat completion.")
		checks.append(_passed("chat", "Non-streaming chat completion succeeded."))
	except Exception as error:
		normalized = _normalize(error, model_config)
		checks.append(_failure("chat", normalized, required=True))
		return _blocked_after_base_failure(checks, CHAT_CHECKS[1:], normalized)

	try:
		chunks = list(model.response_stream(messages=[Message(role="user", content="Reply with the word OK.")]))
		if not any(_response_content(chunk) for chunk in chunks):
			raise ValueError("The provider returned no streamed content.")
		checks.append(_passed("streaming", "Streaming chat completion succeeded."))
	except Exception as error:
		checks.append(_failure("streaming", _normalize(error, model_config), required=True))

	tool = _synthetic_tool([])
	try:
		model.response(
			messages=[Message(role="user", content="Acknowledge the available tool without calling it.")],
			tools=[tool],
			tool_choice="none",
		)
		checks.append(_passed("tool_declaration", "Synthetic tool declaration was accepted."))
	except Exception as error:
		normalized = _normalize(error, model_config)
		checks.append(_failure("tool_declaration", normalized, required=True))
		checks.append(_blocked("tool_call", normalized.message, required=True))
		return _run_optional_chat_checks(checks, model, model_config)

	invocations: list[dict[str, Any]] = []
	try:
		response = model.response(
			messages=[Message(role="user", content="Call the synthetic no-op tool exactly once, then report its result.")],
			tools=[_synthetic_tool(invocations)],
			tool_choice={"type": "function", "function": {"name": SYNTHETIC_TOOL_NAME}},
		)
		if len(invocations) != 1:
			raise ValueError(f"The provider did not complete the synthetic tool call (invocations={len(invocations)}).")
		if _response_content(response) is None:
			raise ValueError("The provider did not request a follow-up completion after the tool result.")
		checks.append(_passed("tool_call", "Synthetic tool call and follow-up completion succeeded."))
	except Exception as error:
		checks.append(_failure("tool_call", _normalize(error, model_config), required=True))

	return _run_optional_chat_checks(checks, model, model_config)


def _run_optional_chat_checks(
	checks: list[dict[str, Any]], model: Any, model_config: dict[str, Any]
) -> dict[str, Any]:
	try:
		response = model.response(
			messages=[Message(role="user", content="Return a JSON object with exactly one key, answer, set to OK.")],
			response_format={"type": "json_object"},
		)
		content = _response_content(response)
		if content is None:
			raise ValueError("The provider returned an empty structured response.")
		json.loads(content)
		checks.append(_passed("structured_output", "Structured JSON output succeeded.", required=False))
	except Exception as error:
		checks.append(_warning("structured_output", _normalize(error, model_config)))

	try:
		large_input = "context probe " * (LARGE_INPUT_CHARS // len("context probe "))
		response = model.response(
			messages=[
				Message(
					role="user",
					content=f"Read this bounded context and reply with the word OK:\n{large_input}",
				)
			],
		)
		if _response_content(response) is None:
			raise ValueError("The provider returned an empty larger-input response.")
		checks.append(_passed("large_input", "Bounded larger-input request succeeded.", required=False))
	except Exception as error:
		checks.append(_warning("large_input", _normalize(error, model_config)))

	return _suite_result(checks)


def _run_embedding_suite(model_config: dict[str, Any]) -> dict[str, Any]:
	try:
		client = create_openai_compatible_client(model_config, timeout=15, max_retries=0)
	except Exception as error:
		return blocked_suite(
			EMBEDDING_CHECKS,
			error,
			provider=model_config.get("provider"),
			model_id=model_config.get("model_id"),
		)

	model_id = normalize_transport_model_id(model_config.get("provider"), model_config["model_id"])
	checks: list[dict[str, Any]] = []
	vector_sets: list[list[list[float]]] = []

	try:
		response = client.embeddings.create(model=model_id, input=["frappe ai single embedding probe"])
		vectors = _embedding_vectors(response)
		if len(vectors) != 1:
			raise ValueError(f"The provider returned {len(vectors)} vectors for one input.")
		vector_sets.append(vectors)
		checks.append(_passed("embedding_single", "Single-input embedding succeeded."))
	except Exception as error:
		normalized = _normalize(error, model_config)
		checks.append(_failure("embedding_single", normalized, required=True))
		return _blocked_after_base_failure(checks, EMBEDDING_CHECKS[1:], normalized)

	try:
		response = client.embeddings.create(model=model_id, input=list(EMBEDDING_BATCH_INPUTS))
		vectors = _embedding_vectors(response)
		if len(vectors) != len(EMBEDDING_BATCH_INPUTS):
			raise ValueError(
				f"The provider returned {len(vectors)} vectors for {len(EMBEDDING_BATCH_INPUTS)} inputs."
			)
		vector_sets.append(vectors)
		checks.append(_passed("embedding_batch", "Multi-input batch embedding succeeded."))
	except Exception as error:
		normalized = _normalize(error, model_config)
		checks.append(_failure("embedding_batch", normalized, required=True))
		checks.append(_blocked("embedding_dimensions", normalized.message, required=True))
		return _suite_result(checks)

	try:
		dimensions = {len(vector) for vectors in vector_sets for vector in vectors}
		if not dimensions or 0 in dimensions or len(dimensions) != 1:
			raise ValueError(f"Embedding dimensions are inconsistent: {sorted(dimensions)}.")
		checks.append(
			_passed(
				"embedding_dimensions",
				f"Embedding response counts and dimension are consistent ({next(iter(dimensions))} dimensions).",
			)
		)
	except Exception as error:
		checks.append(_failure("embedding_dimensions", _normalize(error, model_config), required=True))

	return _suite_result(checks)


def _synthetic_tool(invocations: list[dict[str, Any]]) -> Function:
	def no_op(**arguments: Any) -> dict[str, Any]:
		invocations.append(arguments)
		return {"ok": True, "probe": "frappe_ai_connection_test"}

	return Function(
		name=SYNTHETIC_TOOL_NAME,
		description="Synthetic no-op used only to verify tool declaration and tool-call round trips.",
		parameters={"type": "object", "properties": {}},
		entrypoint=no_op,
		skip_entrypoint_processing=True,
	)


def _blocked_after_base_failure(
	checks: list[dict[str, Any]], names: tuple[str, ...], error: NormalizedProviderError
) -> dict[str, Any]:
	checks.extend(_blocked(name, error.message, required=name not in OPTIONAL_CHAT_CHECKS) for name in names)
	return _suite_result(checks)


def _normalize(error: BaseException | str, model_config: dict[str, Any]) -> NormalizedProviderError:
	return normalize_provider_error(
		error,
		provider=model_config.get("provider"),
		model_id=model_config.get("model_id"),
	)


def _response_content(response: Any) -> str | None:
	content = response.get("content") if isinstance(response, dict) else getattr(response, "content", None)
	if content is None:
		return None
	return content if isinstance(content, str) else str(content)


def _embedding_vectors(response: Any) -> list[list[float]]:
	data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
	if data is None:
		raise ValueError("The provider returned no embedding data.")
	vectors: list[list[float]] = []
	for entry in data:
		vector = entry.get("embedding") if isinstance(entry, dict) else getattr(entry, "embedding", None)
		if not isinstance(vector, (list, tuple)):
			raise ValueError("The provider returned an invalid embedding vector.")
		vectors.append([float(value) for value in vector])
	return vectors


def _passed(name: str, message: str, *, required: bool = True, details: dict[str, Any] | None = None) -> dict[str, Any]:
	return _check(name, "passed", required=required, message=message, details=details)


def _warning(name: str, error: NormalizedProviderError) -> dict[str, Any]:
	return _check(name, "warning", required=False, message=error.message, error=error)


def _failure(name: str, error: NormalizedProviderError, *, required: bool) -> dict[str, Any]:
	return _check(name, "failed", required=required, message=error.message, error=error)


def _blocked(name: str, message: str, *, required: bool) -> dict[str, Any]:
	return _check(name, "blocked", required=required, message=f"Blocked because a prerequisite failed: {message}")


def _check(
	name: str,
	status: str,
	*,
	required: bool,
	message: str,
	error: NormalizedProviderError | None = None,
	details: dict[str, Any] | None = None,
) -> dict[str, Any]:
	result: dict[str, Any] = {"name": name, "status": status, "required": required, "message": message}
	if error is not None:
		result.update(
			error=error.as_dict(),
			code=error.code,
			status_code=error.status_code,
			retryable=error.retryable,
		)
		if error.diagnostics:
			result["diagnostics"] = error.diagnostics
	if details:
		result["details"] = details
	return result


def _suite_result(checks: list[dict[str, Any]]) -> dict[str, Any]:
	warnings = [f"{check['name']}: {check['message']}" for check in checks if check["status"] == "warning"]
	return {
		"ok": all(check["status"] == "passed" for check in checks if check["required"]),
		"checks": checks,
		"warnings": warnings,
	}
