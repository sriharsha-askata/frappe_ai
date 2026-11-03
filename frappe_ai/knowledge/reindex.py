# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Rebuild the search index from the database.

The index is a disposable copy of `AI Knowledge Chunk` (ADR 0002), so anything that changes
how vectors are made or stored is applied by rebuilding it: switching the vector backend,
changing the embedding model, or recovering a damaged index. Run it explicitly:

	bench --site <site> execute frappe_ai.knowledge.reindex.rebuild_knowledge_index
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

CHUNK_DOCTYPE = "AI Knowledge Chunk"
REINDEX_PAGE_SIZE = 500


def rebuild_knowledge_index() -> dict[str, Any]:
	"""Re-embed every chunk and replace the index in the active vector backend.

	Probes the embedding model for its vector width, rebuilds the knowledge index, drops the
	session-attachment index (users re-attach files to refill it), then verifies the row count
	and the stored embedding identity before returning."""
	from frappe_ai.knowledge import index
	from frappe_ai.knowledge.embedder import embed_texts, embedding_identity, probe_dimension
	from frappe_ai.vector.registry import configured_name

	_reset_persisted_dimension()
	dimension = probe_dimension()
	index.chunks.drop()
	index.attachments.drop()
	index.chunks.ensure(dimension)

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
		index.chunks.add(
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

	indexed_count = index.chunks.count()
	if indexed_count != chunk_count:
		frappe.throw(
			_("Rebuild indexed {0} chunks but expected {1}. The search index may be incomplete.").format(
				indexed_count, chunk_count
			),
			title=_("Index Rebuild Incomplete"),
		)
	if index.chunks.metadata() != embedding_identity(dimension):
		frappe.throw(
			_("The rebuilt index metadata is invalid. Rebuild the knowledge index again."),
			title=_("Embedding Index Mismatch"),
		)

	return {
		"status": "completed",
		"backend": configured_name(),
		"dimension": dimension,
		"chunks": chunk_count,
	}


def _reset_persisted_dimension() -> None:
	"""Forget the recorded vector width so a different embedding model can be adopted."""
	frappe.db.set_single_value("AI Settings", "embedding_dimension", 0, update_modified=False)
	frappe.clear_document_cache("AI Settings", "AI Settings")


rebuild_index = rebuild_knowledge_index
