# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Ephemeral, session-scoped vector store for oversized chat attachments.

Separate from the curated KB store (store.py) so chat files never surface in
agent knowledge-base search. Reads are scoped by `session`; the session-owner
check in load_session() is the authorization boundary, enforced before any
search runs. The `content` column is the source of truth; this index is
disposable and rebuildable.

Ported verbatim from `flow.knowledge.attachment_store`
(`apps/flow/flow/knowledge/attachment_store.py`).
"""

from __future__ import annotations

from typing import Any

import frappe
import pyarrow as pa
from frappe import _

from frappe_ai.knowledge.store import (
	_connect,
	_ensure_fts_index,
	_index_metadata,
	_quote,
	_validate_index_metadata,
	_vector_dim,
	index_metadata_for_table,
)

TABLE_NAME = "chat_attachment_chunks"
MAX_SEARCH_LIMIT = 50


def index_metadata() -> dict[str, Any]:
	"""Return the fixed embedding identity recorded in the attachment table."""
	db = _connect()
	if TABLE_NAME not in db.list_tables().tables:
		return {}
	return index_metadata_for_table(db.open_table(TABLE_NAME))


def ensure_table(dimension: int):
	"""Open the chunks table for the given vector width, creating it if missing.

	On a width or fixed-model metadata mismatch the table is dropped and recreated —
	chat chunks are ephemeral and rebuildable, so they are not worth preserving
	across an embedding-index change.
	"""
	if not isinstance(dimension, int) or dimension < 1:
		raise ValueError(f"Invalid embedding dimension: {dimension!r}")

	db = _connect()
	if TABLE_NAME in db.list_tables().tables:
		table = db.open_table(TABLE_NAME)
		if _vector_dim(table) == dimension:
			try:
				_validate_index_metadata(table, dimension)
			except frappe.ValidationError:
				db.drop_table(TABLE_NAME)
			else:
				return table
		if TABLE_NAME in db.list_tables().tables:
			db.drop_table(TABLE_NAME)

	schema = pa.schema(
		[
			pa.field("session", pa.string()),
			pa.field("attachment", pa.string()),
			pa.field("content", pa.string()),
			pa.field("vector", pa.list_(pa.float32(), dimension)),
		],
		metadata=_index_metadata(dimension),
	)
	return db.create_table(TABLE_NAME, schema=schema, exist_ok=True)


def add(session: str, attachment: str, chunks: list[str], vectors: list[list[float]]) -> None:
	"""Insert one attachment's chunks. The table must already exist (ensure_table)."""
	if len(chunks) != len(vectors):
		raise ValueError(f"chunks/vectors length mismatch: {len(chunks)} != {len(vectors)}")
	if not chunks:
		return
	rows = [
		{
			"session": session,
			"attachment": attachment,
			"content": content,
			"vector": [float(v) for v in vector],
		}
		for content, vector in zip(chunks, vectors, strict=True)
	]
	table = _open_table()
	table.add(rows)
	_ensure_fts_index(table)


def search(
	vector: list[float], *, session: str, text: str | None = None, limit: int = 8
) -> list[dict[str, Any]]:
	"""Best-matching chunks within one session, most relevant first.

	With `text`, runs hybrid search (vector + full-text, rank-fused); otherwise
	vector-only. Returns [{content, score}] with higher score = better match. Scoping
	to `session` is mandatory — there is no unscoped search.
	"""
	if not session:
		raise ValueError("search requires a session scope")
	if not _table_exists():
		return []

	table = _open_table()
	vector = [float(v) for v in vector]
	expected_dimension = _vector_dim(table)
	if len(vector) != expected_dimension:
		frappe.throw(
			_("Query vector has dimension {0}, but the attachment index uses {1}.").format(
				len(vector), expected_dimension
			),
			title=_("Embedding Dimension Mismatch"),
		)
	if table.count_rows() == 0:
		return []

	limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
	if text:
		_ensure_fts_index(table)
		query = table.search(query_type="hybrid", vector_column_name="vector").vector(vector).text(text)
	else:
		query = table.search(vector, vector_column_name="vector")
	query = query.distance_type("cosine").where(f"session = {_quote(session)}", prefilter=True).limit(limit)
	return [
		{"content": row["content"], "score": row["_relevance_score"] if text else 1.0 - row["_distance"]}
		for row in query.to_list()
	]


def delete(*, session: str | list[str] | None = None, attachment: str | None = None) -> None:
	"""Delete rows matching ALL given criteria. At least one is required.
	`session` may be a single id or a list of ids (an empty list is a no-op)."""
	if session is None and attachment is None:
		raise ValueError("delete() requires session and/or attachment")
	if isinstance(session, list) and not session:
		return
	if not _table_exists():
		return

	conditions = []
	if session is not None:
		if isinstance(session, str):
			conditions.append(f"session = {_quote(session)}")
		else:
			conditions.append(f"session IN ({', '.join(_quote(s) for s in session)})")
	if attachment is not None:
		conditions.append(f"attachment = {_quote(attachment)}")
	_open_table().delete(" AND ".join(conditions))


def drop_table() -> None:
	db = _connect()
	if TABLE_NAME in db.list_tables().tables:
		db.drop_table(TABLE_NAME)


def _table_exists() -> bool:
	return TABLE_NAME in _connect().list_tables().tables


def _open_table():
	db = _connect()
	table = db.open_table(TABLE_NAME)
	if not index_metadata_for_table(table):
		# Legacy table created before embedding metadata was introduced — drop it so
		# ensure_table recreates it on the next indexing call.
		db.drop_table(TABLE_NAME)
		raise frappe.ValidationError(_("Attachment index is from a previous version and has been cleared."))
	_validate_index_metadata(table)
	return table
