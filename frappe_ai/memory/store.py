# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Vectorless LanceDB FTS index over AI Agent Memory rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
import lancedb
import pyarrow as pa

from frappe_ai.knowledge.store import db_path

if TYPE_CHECKING:
	from frappe_ai.frappe_ai.doctype.ai_agent_memory.ai_agent_memory import AIAgentMemory

TABLE_NAME = "memories"
FTS_FIELD = "search_text"


def _search_text(doc: "AIAgentMemory") -> str:
	content = (doc.content or "").strip()
	keywords = (doc.keywords or "").strip()
	return f"{content}\n{keywords}" if keywords else content


def sync(doc: "AIAgentMemory") -> None:
	"""Upsert an Active memory into the index; remove non-active rows."""
	try:
		table = _table()
		table.delete(f"id = {_quote(doc.name)}")
		if doc.status == "Active":
			table.add(
				[
					{
						"id": doc.name,
						"agent": doc.agent or "",
						"scope": doc.scope or "",
						"user": doc.user or "",
						"search_text": _search_text(doc),
					}
				]
			)
			_ensure_fts_index(table)
	except Exception:
		frappe.log_error(title="AI agent memory index sync failed")


def remove(name: str) -> None:
	try:
		db = lancedb.connect(db_path())
		if TABLE_NAME in db.list_tables().tables:
			db.open_table(TABLE_NAME).delete(f"id = {_quote(name)}")
	except Exception:
		frappe.log_error(title="AI agent memory index cleanup failed")


def search(query: str, *, agent: str, user: str, limit: int) -> list[str]:
	"""Return best FTS matches among shared + this user's personal memories."""
	try:
		db = lancedb.connect(db_path())
		if TABLE_NAME not in db.list_tables().tables:
			return []
		table = db.open_table(TABLE_NAME)
		if table.count_rows() == 0:
			return []
		_ensure_fts_index(table)
		scope = f"agent = {_quote(agent)} AND (scope = 'Agent' OR user = {_quote(user)})"
		rows = table.search(query, query_type="fts").where(scope, prefilter=True).limit(limit).to_list()
		return [row["id"] for row in rows]
	except Exception:
		frappe.log_error(title="AI agent memory search failed")
		return []


def drop_table() -> None:
	db = lancedb.connect(db_path())
	if TABLE_NAME in db.list_tables().tables:
		db.drop_table(TABLE_NAME)


def _table():
	db = lancedb.connect(db_path())
	if TABLE_NAME in db.list_tables().tables:
		return db.open_table(TABLE_NAME)
	schema = pa.schema(
		[
			pa.field("id", pa.string()),
			pa.field("agent", pa.string()),
			pa.field("scope", pa.string()),
			pa.field("user", pa.string()),
			pa.field(FTS_FIELD, pa.string()),
		]
	)
	return db.create_table(TABLE_NAME, schema=schema, exist_ok=True)


def _ensure_fts_index(table) -> None:
	if any(str(index.index_type) == "FTS" for index in table.list_indices()):
		return
	table.create_fts_index(FTS_FIELD, replace=True)


def _quote(value: str) -> str:
	if not isinstance(value, str):
		raise ValueError(f"Expected string filter value, got {type(value).__name__}")
	escaped = value.replace("'", "''")
	return f"'{escaped}'"
