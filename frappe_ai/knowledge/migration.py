# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""One-time migration helpers for the fixed Ollama embedding index."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

CHUNK_DOCTYPE = "AI Knowledge Chunk"
LEGACY_SETTINGS_FIELD = "embedding_model"
REINDEX_PAGE_SIZE = 500


def migrate_legacy_embedding_configuration() -> dict[str, Any]:
	"""Detect legacy model selection and reset the persisted width for migration.

	The old ``embedding_model`` value is intentionally retained until
	``rebuild_knowledge_index`` completes. This makes a failed rebuild retryable
	without pretending that the old vectors are compatible with Ollama.
	"""
	if not frappe.db.exists("DocType", "AI Settings"):
		return {"status": "not_installed", "legacy_model": None}

	legacy_model = _read_legacy_embedding_model()
	if not legacy_model:
		return {"status": "already_fixed", "legacy_model": None}

	_reset_persisted_dimension()
	return {"status": "legacy_configuration_found", "legacy_model": legacy_model}


def rebuild_knowledge_index() -> dict[str, Any]:
	"""Re-embed every MariaDB chunk and rebuild the disposable LanceDB index.

	This is intentionally explicit and operator-invoked. It performs a real probe,
	uses the fixed Ollama model, verifies row count and metadata, and removes the
	legacy setting only after the new index is complete.
	"""
	from frappe_ai.knowledge import store
	from frappe_ai.knowledge.embedder import embed_texts, probe_dimension

	legacy_model = _read_legacy_embedding_model()
	_reset_persisted_dimension()
	dimension = probe_dimension()
	store.drop_table()
	store.ensure_table_for_dimension(dimension)

	chunk_count = 0
	start = 0
	while True:
		rows = frappe.get_all(
			CHUNK_DOCTYPE,
			fields=["name", "knowledge_base", "source", "content"],
			order_by="name asc",
			limit_start=start,
			limit_page_length=REINDEX_PAGE_SIZE,
		)
		if not rows:
			break

		vectors = embed_texts([row.content for row in rows])
		store.add(
			[
				{
					"id": int(row.name),
					"kb": row.knowledge_base,
					"source": row.source,
					"content": row.content,
					"vector": vector,
				}
				for row, vector in zip(rows, vectors, strict=True)
			]
		)
		chunk_count += len(rows)
		start += REINDEX_PAGE_SIZE
		if len(rows) < REINDEX_PAGE_SIZE:
			break

	indexed_count = store._open_table().count_rows()
	if indexed_count != chunk_count:
		frappe.throw(
			_("Rebuild indexed {0} chunks but expected {1}. The LanceDB store may be incomplete.").format(
				indexed_count, chunk_count
			),
			title=_("Embedding Rebuild Incomplete"),
		)
	if store.index_metadata() != {"provider": "ollama", "model": "nomic-embed-text", "dimension": dimension}:
		frappe.throw(
			_("The rebuilt index metadata is invalid. Rebuild the knowledge store again."),
			title=_("Embedding Index Mismatch"),
		)

	_clear_legacy_embedding_model()
	return {
		"status": "completed",
		"legacy_model": legacy_model,
		"dimension": dimension,
		"chunks": chunk_count,
	}


def _read_legacy_embedding_model() -> str | None:
	"""Read the old Single value even after its DocType field is removed."""
	try:
		if frappe.get_meta("AI Settings").get_field(LEGACY_SETTINGS_FIELD):
			return frappe.db.get_single_value("AI Settings", LEGACY_SETTINGS_FIELD) or None
	except Exception:
		pass

	try:
		singles = frappe.qb.DocType("Singles")
		rows = (
			frappe.qb.from_(singles)
			.select(singles.value)
			.where((singles.doctype == "AI Settings") & (singles.field == LEGACY_SETTINGS_FIELD))
			.limit(1)
		).run(as_dict=True)
	except Exception:
		return None
	return (rows[0].get("value") or None) if rows else None


def _reset_persisted_dimension() -> None:
	frappe.db.set_single_value("AI Settings", "embedding_dimension", 0, update_modified=False)
	frappe.clear_document_cache("AI Settings", "AI Settings")


def _clear_legacy_embedding_model() -> None:
	singles = frappe.qb.DocType("Singles")
	frappe.qb.from_(singles).delete().where(
		(singles.doctype == "AI Settings") & (singles.field == LEGACY_SETTINGS_FIELD)
	).run()
	frappe.clear_document_cache("AI Settings", "AI Settings")


rebuild_index = rebuild_knowledge_index
