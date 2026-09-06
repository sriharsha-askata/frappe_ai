# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Code-first knowledge API.

Build and populate an AI Knowledge Base from Python — attach text, files, URLs,
or DocType data without the desk UI. Ingestion is synchronous: when an add_*
call returns, the content is embedded, indexed, and ready to search.

Ported verbatim (module-path substitution only) from `flow.knowledge.knowledge`
(`apps/flow/flow/knowledge/knowledge.py`).
"""

from __future__ import annotations

import frappe
from frappe import _

KB_DOCTYPE = "AI Knowledge Base"
SOURCE_DOCTYPE = "AI Knowledge Source"
_TITLE_LIMIT = 140


class Knowledge:
	"""A handle to a named AI Knowledge Base. Construction is get-or-create by title."""

	def __init__(self, title: str, *, description: str | None = None) -> None:
		self.name = _ensure_kb(title, description)

	def add_text(self, content: str, *, title: str | None = None) -> str:
		return self._ingest(source_type="Text", title=title or _snippet(content), content=content)

	def add_file(self, file_url: str, *, title: str | None = None) -> str:
		return self._ingest(source_type="File", title=title or file_url, file=file_url)

	def add_files(self, file_urls: list[str], *, title: str | None = None) -> list[str]:
		"""Load multiple files into this knowledge base in input order."""
		return [self.add_file(file_url, title=title) for file_url in file_urls]

	@classmethod
	def load_files(
		cls,
		file_urls: list[str],
		*,
		kb_title: str,
		description: str | None = None,
	) -> "Knowledge":
		"""Get or create a knowledge base and load the supplied files into it."""
		instance = cls(title=kb_title, description=description)
		instance.add_files(file_urls)
		return instance

	def add_local_file(self, path: str, *, title: str | None = None) -> str:
		import os

		from frappe_ai.knowledge.extract import _extract_by_extension

		extension = os.path.splitext(path)[1].lower().lstrip(".")
		with open(path, "rb") as handle:
			text = _extract_by_extension(handle.read(), extension)
		return self.add_text(text, title=title or os.path.basename(path))

	def add_url(self, url: str, *, title: str | None = None) -> str:
		return self._ingest(source_type="URL", title=title or url, url=url)

	def add_doctype(
		self,
		reference_doctype: str,
		*,
		content_fields: list[str],
		filters: dict | None = None,
		title: str | None = None,
		auto_sync: bool = False,
	) -> str:
		return self._ingest(
			source_type="DocType",
			title=title or reference_doctype,
			reference_doctype=reference_doctype,
			content_fields=", ".join(content_fields),
			filters=frappe.as_json(filters) if filters else None,
			auto_sync=int(auto_sync),
		)

	def _ingest(self, **fields: object) -> str:
		from frappe_ai.frappe_ai.doctype.ai_knowledge_source.ai_knowledge_source import (
			AIKnowledgeSource,
			EVENT_AFTER_SOURCE_CREATE,
		)
		from frappe_ai.knowledge.ingest import ingest_source

		source_type = str(fields.get("source_type") or "")
		file_url = str(fields.get("file") or "") if source_type == "File" else ""
		content_hash = AIKnowledgeSource.content_hash_for_file(file_url) if file_url else None
		if content_hash:
			existing_name = AIKnowledgeSource.find_existing_completed(
				self.name, source_type, content_hash
			)
			if existing_name:
				# A deduplicated load is still an ingestion attempt from the caller's
				# perspective, so expose the same start/outcome audit pair as a
				# normal synchronous load.  No re-index is necessary.
				AIKnowledgeSource.log_event(
					"start",
					source=existing_name,
					knowledge_base=self.name,
					source_type=source_type,
				)
				AIKnowledgeSource.log_event(
					"dedup_hit",
					source=existing_name,
					knowledge_base=self.name,
					source_type=source_type,
					content_hash=content_hash,
				)
				AIKnowledgeSource.dispatch_event(
					EVENT_AFTER_SOURCE_CREATE,
					source=existing_name,
					knowledge_base=self.name,
					source_type=source_type,
				)
				return existing_name

		candidate = frappe._dict({"doctype": SOURCE_DOCTYPE, "knowledge_base": self.name, **fields})
		veto = AIKnowledgeSource.before_create(candidate)
		if veto and veto.get("cancelled"):
			frappe.throw(
				_("Knowledge source creation was cancelled by a consumer hook."),
				title=_("Knowledge Source Cancelled"),
			)

		source = frappe.get_doc(candidate)
		source.flags.skip_auto_ingest = True
		source.insert()
		AIKnowledgeSource.after_create(source)
		AIKnowledgeSource.log_event(
			"source_created",
			source=source.name,
			knowledge_base=self.name,
			source_type=source_type,
			content_hash=content_hash,
		)
		ingest_source(source.name)
		return source.name


def _ensure_kb(title: str, description: str | None) -> str:
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Knowledge base title is required."), title=_("Invalid Knowledge Base"))
	if frappe.db.exists(KB_DOCTYPE, title):
		return title
	return frappe.get_doc({"doctype": KB_DOCTYPE, "title": title, "description": description}).insert().name


def _snippet(content: str) -> str:
	text = " ".join((content or "").split())
	return text[:_TITLE_LIMIT] or "Text"
