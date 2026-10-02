# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Knowledge Source` — ported from `flow`'s `Flow Knowledge Source`
(see `apps/flow/flow/flow/doctype/flow_knowledge_source/flow_knowledge_source.py`).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method, cint
from frappe.utils.file_manager import get_content_hash

from frappe_ai.utils.system_generated import block_delete, validate_immutable

EVENT_BEFORE_SOURCE_CREATE = "ai_knowledge_before_source_create"
EVENT_AFTER_SOURCE_CREATE = "ai_knowledge_after_source_create"
EVENT_ON_INDEX_FAILED = "ai_knowledge_on_index_failed"
LOGGER_NAME = "frappe_ai.knowledge"

_LIFECYCLE_INFO_EVENTS = {"start", "source_created", "dedup_hit", "indexed"}

REQUIRED_INPUT = {
	"Text": "content",
	"File": "file",
	"URL": "url",
	"DocType": "reference_doctype",
}


class AIKnowledgeSource(Document):
	@staticmethod
	def content_hash_for_file(file_url: str) -> str | None:
		"""Return a file's persisted hash, computing it for legacy File rows when needed."""
		if not file_url:
			return None

		try:
			file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
			if not file_name and frappe.db.exists("File", file_url):
				file_name = file_url
			if not file_name:
				return None

			file_doc = frappe.get_doc("File", file_name)
			if file_doc.content_hash:
				return file_doc.content_hash
			# Passing an empty encoding list keeps File.get_content from decoding
			# text, so the fallback hash is over the original bytes.
			try:
				content = file_doc.get_content(encodings=[])
			except TypeError:
				content = file_doc.get_content()
			if isinstance(content, str):
				content = content.encode()
			return get_content_hash(content)
		except Exception:
			# A missing or unreadable attachment must not make loading a source fail.
			return None

	#: Max references per `File` lookup, to keep each query's `IN` list bounded.
	_FILE_LOOKUP_CHUNK = 500

	@classmethod
	def find_existing_completed(cls, kb: str, source_type: str, content_hash: str) -> str | None:
		"""Find a completed source with the same content in the same knowledge base.

		The source DocType intentionally has no hash column. File hashes belong to the
		File DocType, so legacy and newly uploaded files can share the same lookup path.

		The candidates' File rows are resolved in batched queries and compared on their
		stored hash. Only a legacy File row with no stored hash falls back to
		`content_hash_for_file`, which reads and hashes the file's bytes. The earliest
		matching source wins, as before.
		"""
		if not content_hash or source_type != "File":
			return None

		rows = frappe.get_all(
			"AI Knowledge Source",
			filters={
				"knowledge_base": kb,
				"source_type": source_type,
				"status": "Completed",
			},
			fields=["name", "file"],
			order_by="creation asc, name asc",
		)
		refs = list(dict.fromkeys(row.get("file") for row in rows if row.get("file")))
		by_url, by_name = cls._file_rows(refs)
		for row in rows:
			ref = row.get("file")
			if not ref:
				continue
			# Same resolution order as `content_hash_for_file`: file_url first, then name.
			file_row = by_url.get(ref) or by_name.get(ref)
			if file_row is None:
				continue
			stored = file_row.get("content_hash") or cls.content_hash_for_file(ref)
			if stored == content_hash:
				return row.get("name")
		return None

	@classmethod
	def _file_rows(cls, refs: list[str]) -> tuple[dict[str, dict], dict[str, dict]]:
		"""Resolve file references (URLs or File names) to File rows, a chunk per query."""
		by_url: dict[str, dict] = {}
		by_name: dict[str, dict] = {}
		for start in range(0, len(refs), cls._FILE_LOOKUP_CHUNK):
			chunk = refs[start : start + cls._FILE_LOOKUP_CHUNK]
			for file_row in frappe.get_all(
				"File",
				or_filters=[["file_url", "in", chunk], ["name", "in", chunk]],
				fields=["name", "file_url", "content_hash"],
			):
				by_name[file_row.name] = file_row
				url = file_row.file_url
				if url:
					current = by_url.get(url)
					# Two File rows can share a URL; prefer one that carries a stored hash.
					if current is None or (not current.content_hash and file_row.content_hash):
						by_url[url] = file_row
		return by_url, by_name

	@staticmethod
	def log_event(event: str, source: str | None = None, **payload: Any) -> None:
		"""Write lifecycle telemetry without allowing observability to affect ingestion."""
		try:
			data = {"source": source, **payload}
			if event in _LIFECYCLE_INFO_EVENTS:
				frappe.logger(LOGGER_NAME).info(event, extra=data)
			elif event == "failed":
				frappe.log_error(
					title=f"Knowledge: failed - {source}",
					message=frappe.as_json({"event": event, **data}),
				)
		except Exception:
			pass

	@staticmethod
	def dispatch_event(event: str, **kwargs: Any) -> Any | None:
		"""Dispatch a named consumer hook; only before hooks can return a value."""
		result = call_hook_method(event, **kwargs)
		if event == EVENT_BEFORE_SOURCE_CREATE:
			return result
		return None

	@classmethod
	def before_create(cls, candidate: frappe._dict) -> dict | None:
		result = cls.dispatch_event(
			EVENT_BEFORE_SOURCE_CREATE,
			candidate=candidate,
			knowledge_base=candidate.get("knowledge_base"),
		)
		return result if isinstance(result, dict) and result.get("cancelled") else None

	def after_create(self) -> None:
		self.dispatch_event(
			EVENT_AFTER_SOURCE_CREATE,
			source=self.name,
			knowledge_base=self.knowledge_base,
			source_type=self.source_type,
		)

	@classmethod
	def on_index_failed(cls, doc: "AIKnowledgeSource", exc: Exception) -> None:
		"""Notify consumers of an indexing failure. A raising hook is logged, never
		propagated: it must not mask the ingestion error that is about to be re-raised."""
		try:
			cls.dispatch_event(
				EVENT_ON_INDEX_FAILED,
				doc=doc,
				exc=exc,
			)
		except Exception:
			try:
				frappe.log_error(
					title="Knowledge: index-failed hook error",
					message=frappe.get_traceback(),
					reference_doctype="AI Knowledge Source",
					reference_name=doc.name,
				)
			except Exception:
				pass

	@staticmethod
	def _file_doc(file: str):
		file_name = frappe.db.get_value("File", {"file_url": file}, "name")
		if not file_name and frappe.db.exists("File", file):
			file_name = file
		return frappe.get_doc("File", file_name) if file_name else None

	@staticmethod
	def _source_summaries(rows: list[dict]) -> list[dict]:
		return [
			{
				"name": row.get("name"),
				"title": row.get("title"),
				"status": row.get("status"),
				"chunk_count": row.get("chunk_count") or 0,
				"is_embedded": row.get("is_embedded") or 0,
				"knowledge_base": row.get("knowledge_base"),
				"kb_enabled": frappe.db.get_value(
					"AI Knowledge Base", row.get("knowledge_base"), "enabled"
				),
				"last_synced_at": row.get("last_synced_at"),
				"error_log": row.get("error_log"),
			}
			for row in rows
		]

	@staticmethod
	@frappe.whitelist()
	def get_file_status(file: str) -> dict:
		frappe.has_permission("AI Knowledge Source", "read", throw=True)
		try:
			file_doc = AIKnowledgeSource._file_doc(file)
			file_url = file_doc.file_url if file_doc else file
			rows = frappe.get_all(
				"AI Knowledge Source",
				filters={"source_type": "File"},
				fields=[
					"name",
					"title",
					"status",
					"chunk_count",
					"is_embedded",
					"knowledge_base",
					"last_synced_at",
					"error_log",
					"file",
				],
			)
			rows = [row for row in rows if row.get("file") in {file, file_url}]
			return {
				"file": file,
				"file_url": file_url,
				"content_hash": AIKnowledgeSource.content_hash_for_file(file_url),
				"is_private": file_doc.is_private if file_doc else None,
				"sources": AIKnowledgeSource._source_summaries(rows),
			}
		except Exception as exc:
			return {"error": str(exc)[:500]}

	@staticmethod
	@frappe.whitelist()
	def get_knowledge_base_inventory(kb_name: str) -> dict:
		frappe.has_permission("AI Knowledge Source", "read", throw=True)
		try:
			exists = bool(frappe.db.exists("AI Knowledge Base", kb_name))
			enabled = frappe.db.get_value("AI Knowledge Base", kb_name, "enabled") if exists else None
			rows = frappe.get_all(
				"AI Knowledge Source",
				filters={"knowledge_base": kb_name},
				fields=[
					"name",
					"title",
					"status",
					"chunk_count",
					"is_embedded",
					"knowledge_base",
					"source_type",
					"file",
					"url",
					"last_synced_at",
					"error_log",
				],
				order_by="creation asc, name asc",
			)
			by_status = {status: 0 for status in ("Pending", "Processing", "Completed", "Failed")}
			for row in rows:
				status = row.get("status")
				if status in by_status:
					by_status[status] += 1
			return {
				"knowledge_base": kb_name,
				"exists": exists,
				"enabled": enabled,
				"sources": rows,
				"totals": {
					"source_count": len(rows),
					"chunk_count": sum(row.get("chunk_count") or 0 for row in rows),
					"by_status": by_status,
				},
			}
		except Exception as exc:
			return {"error": str(exc)[:500]}

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		auto_sync: DF.Check
		chunk_count: DF.Int
		is_embedded: DF.Check
		chunk_overlap: DF.Int
		chunk_size: DF.Int
		content: DF.LongText | None
		content_fields: DF.SmallText | None
		error_log: DF.LongText | None
		file: DF.Attach | None
		filters: DF.JSON | None
		is_system_generated: DF.Check
		knowledge_base: DF.Link
		last_synced_at: DF.Datetime | None
		reference_doctype: DF.Link | None
		source_type: DF.Literal["Text", "File", "URL", "DocType"]
		status: DF.Literal["Pending", "Processing", "Completed", "Failed"]
		title: DF.Data
		url: DF.Data | None
	# end: auto-generated types

	def validate(self):
		fieldname = REQUIRED_INPUT.get(self.source_type)
		if fieldname and not self.get(fieldname):
			frappe.throw(
				_("{0} is required for a {1} source.").format(
					_(self.meta.get_label(fieldname)), _(self.source_type)
				),
				frappe.MandatoryError,
			)
		if self.source_type == "DocType" and not (self.content_fields or "").strip():
			frappe.throw(
				_("Content Fields is required for a DocType source."),
				frappe.MandatoryError,
			)
		self._validate_chunking()
		validate_immutable(self, ("source_type", "knowledge_base"))

	def _validate_chunking(self):
		"""0 inherits the global default. Only validate values set on the source itself."""
		if self.chunk_size and self.chunk_overlap and cint(self.chunk_overlap) >= cint(self.chunk_size):
			frappe.throw(_("Chunk Overlap must be smaller than Chunk Size."), title=_("Invalid Chunking"))

	def after_insert(self):
		if self.flags.skip_auto_ingest:
			return
		from frappe_ai.knowledge.ingest import enqueue_ingestion

		enqueue_ingestion(self.name)

	def on_trash(self):
		block_delete(self)
		from frappe_ai.knowledge.ingest import purge_source

		purge_source(self.name)

	@frappe.whitelist()
	def resync(self, rebuild: bool = False):
		"""rebuild forces a full re-chunk; used when chunk settings change and the
		existing chunks are stale."""
		from frappe_ai.knowledge.ingest import enqueue_ingestion

		# `run_doc_method` only enforces `read`, but re-ingestion re-chunks and
		# re-embeds the whole source against the embedding service.
		self.check_permission("write")
		self.db_set("status", "Pending", update_modified=False)
		enqueue_ingestion(self.name, rebuild=bool(rebuild))

	@frappe.whitelist()
	def reconcile(self):
		from frappe_ai.knowledge.ingest import enqueue_reconciliation

		self.check_permission("write")
		enqueue_reconciliation(self.name)
