# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Session Attachment` stores files attached to a conversation.

Extraction now goes through `frappe_ai.knowledge.extract.extract_file`
(`FILE_EXTENSIONS`) — PDF, DOCX, XLSX, HTML, OCR, plain text and, when a
transcription model is configured, audio: the same set `AI Knowledge Source` File sources use. Retrieval mode (chunk + embed oversized files
into the attachment search index) is wired in `AISession.persist_turn`/
`_index_retrieval_attachments`/`build_prompt_messages`.
"""

from __future__ import annotations

import os
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_ai.knowledge.extract import FILE_EXTENSIONS, extract_file
from frappe_ai.knowledge.transcribe import is_audio_extension, require_transcription_model

CACHE_PREFIX = "chat_attachment"
CACHE_TTL = 3600


class AISessionAttachment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		extracted_text: DF.LongText | None
		file: DF.Link
		file_name: DF.Data | None
		file_size: DF.Int
		mode: DF.Literal["Inline", "Retrieval"]
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		run: DF.Link | None
	# end: auto-generated types


def extract_attachment(file: str) -> dict[str, Any]:
	"""Validate file ownership and type, then return extracted text and metadata.

	Raise extraction errors at upload time."""
	file_doc = frappe.get_doc("File", file)
	if not frappe.has_permission("File", "read", doc=file_doc):
		frappe.throw(_("Not permitted to use this file."), frappe.PermissionError)

	extension = _extension(file_doc.file_name)
	if is_audio_extension(extension):
		require_transcription_model()
	elif extension not in FILE_EXTENSIONS:
		frappe.throw(
			_("Unsupported file type: .{0}").format(extension or "?"),
			title=_("Unsupported File"),
		)

	text = extract_file(file_doc)
	if not text:
		frappe.throw(_("No readable text found in this file."), title=_("Empty File"))

	return {
		"file": file_doc.name,
		"file_name": file_doc.file_name,
		"file_size": file_doc.file_size,
		"extracted_text": text,
	}


def _extension(file_name: str | None) -> str:
	return os.path.splitext(file_name or "")[1].lower().lstrip(".")


def _cache_key(file: str) -> str:
	return f"{CACHE_PREFIX}:{frappe.session.user}:{file}"


def stage_attachment(file: str) -> dict[str, Any]:
	"""Cache extracted text at upload and return lightweight composer metadata."""
	data = extract_attachment(file)
	frappe.cache.set_value(_cache_key(file), data, expires_in_sec=CACHE_TTL)
	return {"file": data["file"], "file_name": data["file_name"], "file_size": data["file_size"]}


def staged_attachment(file: str) -> dict[str, Any] | None:
	"""Read the extraction staged at upload time. None if it has expired."""
	return frappe.cache.get_value(_cache_key(file))


def resolve_attachment(file: str) -> dict[str, Any]:
	"""Materialize an attachment at send time. Prefers the upload-time staged extraction;
	on a cache miss it re-extracts, which also re-checks the caller's read permission."""
	staged = staged_attachment(file)
	if staged:
		return staged
	if is_audio_extension(_extension(frappe.db.get_value("File", file, "file_name"))):
		# Transcribing again would repeat the cost and delay without the user knowing.
		frappe.throw(_("This audio upload has expired. Attach the file again."), title=_("Attach Again"))
	return extract_attachment(file)
