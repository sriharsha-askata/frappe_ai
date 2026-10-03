# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Retrieve knowledge-base-scoped hits and hydrate them from MariaDB.

Agent bindings authorize ordinary KB content; an empty or disabled KB scope
fails closed. Chunks derived from DocType records also require the acting user's
read permission on each source document."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

KB_DOCTYPE = "AI Knowledge Base"
CHUNK_DOCTYPE = "AI Knowledge Chunk"
DEFAULT_LIMIT = 5


def retrieve_attachments(query: str, *, session: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
	"""Best-matching chunks from the files attached to `session`, most relevant first.

	Scoping to `session` is the boundary (enforced upstream by the session-owner check).
	Returns [{content, score}]; empty on a blank query or when nothing is indexed.
	"""
	query = (query or "").strip()
	if not query or not session:
		return []

	from frappe_ai.knowledge import attachment_store
	from frappe_ai.knowledge.embedder import embed_texts

	search_type = frappe.get_cached_value("AI Settings", "AI Settings", "search_type")
	text = query if search_type != "Vector" else None

	(vector,) = embed_texts([query])
	return attachment_store.search(vector, session=session, text=text, limit=limit)


def retrieve(query: str, *, kbs: list[str], limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
	"""Return the best-matching chunks within `kbs`, most relevant first.

	`kbs` must be non-empty — an empty scope is refused, not read as "all".
	"""
	if not kbs:
		frappe.throw(
			_("Knowledge search requires at least one knowledge base."),
			title=_("No Knowledge Base"),
		)
	query = (query or "").strip()
	if not query:
		return []

	kbs = _enabled_kbs(kbs)
	if not kbs:
		return []

	from frappe_ai.knowledge import store
	from frappe_ai.knowledge.embedder import embed_texts

	search_type = frappe.get_cached_value("AI Settings", "AI Settings", "search_type")
	text = query if search_type != "Vector" else None

	(vector,) = embed_texts([query])
	hits = store.search(vector, text=text, kbs=kbs, limit=limit)
	if not hits:
		return []

	chunks = _permitted(_hydrate({int(hit["id"]) for hit in hits}))
	results: list[dict[str, Any]] = []
	for hit in hits:
		chunk = chunks.get(int(hit["id"]))
		if chunk is None:
			continue
		results.append(
			{
				"chunk": chunk["name"],
				"chunk_index": chunk.get("chunk_index"),
				"content": chunk["content"],
				"score": hit["score"],
				"source": chunk["source"],
				"reference_doctype": chunk.get("reference_doctype"),
				"reference_name": chunk.get("reference_name"),
			}
		)
	return results


def _enabled_kbs(kbs: list[str]) -> list[str]:
	"""Keep only knowledge bases that still exist and are enabled. Disabling or
	deleting a KB removes it from every bound agent's reach without re-binding."""
	return frappe.get_all(KB_DOCTYPE, filters={"name": ["in", kbs], "enabled": 1}, pluck="name")


def _permitted(chunks: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
	"""Drop chunks whose source document the running user cannot read.

	Only chunks that name a source document are checked — see this module's
	docstring for why DocType-sourced content cannot rely on the KB binding as
	its authorization. Permissions are resolved once per distinct document, since
	a single document usually contributes several chunks to one result set.
	"""
	permitted: dict[int, dict[str, Any]] = {}
	seen: dict[tuple[str, str], bool] = {}
	for chunk_id, chunk in chunks.items():
		doctype = chunk.get("reference_doctype")
		name = chunk.get("reference_name")
		if not doctype or not name:
			permitted[chunk_id] = chunk
			continue
		key = (doctype, name)
		if key not in seen:
			seen[key] = bool(frappe.has_permission(doctype, "read", name))
		if seen[key]:
			permitted[chunk_id] = chunk
	return permitted


def _hydrate(ids: set[int]) -> dict[int, dict[str, Any]]:
	rows = frappe.get_all(
		CHUNK_DOCTYPE,
		filters={"name": ["in", list(ids)]},
		fields=["name", "chunk_index", "content", "source", "reference_doctype", "reference_name"],
	)
	return {int(row["name"]): row for row in rows}
