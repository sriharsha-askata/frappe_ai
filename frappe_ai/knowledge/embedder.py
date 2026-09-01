# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Embedding calls for the knowledge store.

Embeddings are deliberately application-wide rather than another user-selected
model. Ollama serves the fixed ``nomic-embed-text`` model through its
OpenAI-compatible API; only the endpoint varies between environments.
"""

from __future__ import annotations

import os
from typing import Any

import frappe
from frappe import _

BATCH_SIZE = 96
EMBED_TIMEOUT = 120
PROBE_TIMEOUT = 30

EMBEDDING_PROVIDER = "ollama"
EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_BASE_URL_ENV = "FRAPPE_AI_OLLAMA_BASE_URL"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"


class EmbeddingServiceUnavailable(frappe.ValidationError):
	"""The fixed Ollama embedding service could not serve a request."""


def embed_texts(texts: list[str]) -> list[list[float]]:
	"""Embed texts with the fixed Ollama model, preserving input order.

	The first successful call records the returned vector width in AI Settings.
	That value is a consistency guard, not a model-selection setting.
	"""
	if not texts:
		return []

	config = _embedding_config()
	vectors: list[list[float]] = []
	for start in range(0, len(texts), BATCH_SIZE):
		vectors.extend(_embed_batch(texts[start : start + BATCH_SIZE], config, timeout=EMBED_TIMEOUT))

	_record_embedding_dimension(vectors)
	return vectors


def probe_dimension() -> int:
	"""Discover and persist the fixed model's vector width with one real request."""
	config = _embedding_config()
	(vector,) = _embed_batch(["dimension probe"], config, timeout=PROBE_TIMEOUT)
	_record_embedding_dimension([vector])
	return len(vector)


def _embedding_config() -> dict[str, Any]:
	"""Return the fixed embedding configuration for the current environment."""
	return {
		"provider": EMBEDDING_PROVIDER,
		"model": EMBEDDING_MODEL,
		"api_key": "ollama",
		"base_url": (os.environ.get(OLLAMA_BASE_URL_ENV) or DEFAULT_OLLAMA_BASE_URL).rstrip("/"),
	}


def _record_embedding_dimension(vectors: list[list[float]]) -> int:
	"""Persist the observed width and reject a mixed embedding space."""
	if not vectors or not vectors[0]:
		frappe.throw(
			_("The embedding service returned an empty vector."),
			title=_("Embedding Failed"),
		)

	dimension = len(vectors[0])
	if any(len(vector) != dimension for vector in vectors):
		frappe.throw(
			_("The embedding service returned inconsistent vector dimensions."),
			title=_("Embedding Dimension Mismatch"),
		)

	if not frappe.db.exists("DocType", "AI Settings"):
		return dimension

	from frappe.utils import cint

	existing = frappe.db.get_single_value("AI Settings", "embedding_dimension")
	# cint converts None → 0 and the "0" string → 0; 0 is the "not yet observed" sentinel.
	stored = cint(existing)
	if stored and stored != dimension:
		frappe.throw(
			_("Configured embedding dimension is {0}, but Ollama returned {1}. Rebuild the knowledge store.").format(
				stored, dimension
			),
			title=_("Embedding Dimension Mismatch"),
		)
	if not stored:
		frappe.db.set_single_value("AI Settings", "embedding_dimension", dimension, update_modified=False)
		frappe.clear_document_cache("AI Settings", "AI Settings")
	return dimension


def _embed_batch(texts: list[str], config: dict[str, Any], *, timeout: int) -> list[list[float]]:
	try:
		entries = _call_openai_compatible(texts, config, timeout=timeout)
	except EmbeddingServiceUnavailable:
		raise
	except Exception as error:
		message = str(error).strip() or type(error).__name__
		raise EmbeddingServiceUnavailable(
			_("Ollama embedding service is unavailable at {0}: {1}").format(config["base_url"], message[:400])
		) from error

	try:
		entries = sorted(entries, key=lambda entry: entry[0])
		vectors = [[float(value) for value in vector] for _, vector in entries]
	except Exception as error:
		raise EmbeddingServiceUnavailable(
			_("Ollama returned an invalid embedding response: {0}").format(str(error)[:400])
		) from error

	if len(vectors) != len(texts):
		frappe.throw(
			_("Embedding provider returned {0} vectors for {1} inputs.").format(len(vectors), len(texts)),
			title=_("Embedding Failed"),
		)
	return vectors


def _call_openai_compatible(
	texts: list[str], config: dict[str, Any], *, timeout: int
) -> list[tuple[int, list[float]]]:
	"""Call Ollama's OpenAI-compatible embeddings endpoint."""
	from frappe_ai.lib.model import create_openai_compatible_client

	client = create_openai_compatible_client(config, timeout=timeout, max_retries=0)
	response = client.embeddings.create(model=config["model"], input=texts)
	return [(entry.index, entry.embedding) for entry in response.data]
