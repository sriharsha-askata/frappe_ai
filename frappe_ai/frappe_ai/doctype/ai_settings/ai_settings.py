# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AISettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		chunk_overlap: DF.Int
		chunk_size: DF.Int
		embedding_dimension: DF.Int
		max_audio_mb: DF.Int
		request_timeout: DF.Int
		search_type: DF.Literal["Hybrid", "Vector"]
		service_status: DF.Data | None
		speech_model: DF.Link | None
		speech_voice: DF.Data | None
		stream_timeout: DF.Int
		transcription_model: DF.Link | None
		transcription_timeout: DF.Int
		vector_backend: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self._validate_chunking()
		self._validate_vector_backend()
		self._validate_audio()

	def _validate_vector_backend(self):
		"""Accept Auto or a backend that is built in or registered by an installed app."""
		from frappe_ai.vector.registry import available_backends

		choice = (self.vector_backend or "Auto").strip()
		self.vector_backend = choice
		if choice.lower() == "auto":
			return
		if choice.lower() not in available_backends():
			frappe.throw(
				_("Unknown vector backend {0}. Use Auto or one of: {1}.").format(
					choice, ", ".join(available_backends())
				),
				title=_("Invalid Vector Backend"),
			)
		if choice.lower() == "pgvector" and frappe.db.db_type != "postgres":
			frappe.throw(
				_("The pgvector backend needs a Postgres site database."), title=_("Invalid Vector Backend")
			)

	def _validate_chunking(self):
		if cint(self.chunk_size) < 1:
			frappe.throw(_("Chunk Size must be at least 1."), title=_("Invalid Chunking"))
		if cint(self.chunk_overlap) >= cint(self.chunk_size):
			frappe.throw(_("Chunk Overlap must be smaller than Chunk Size."), title=_("Invalid Chunking"))

	def _validate_audio(self):
		"""The audio models must be usable, and the limits must not be negative (0 means default)."""
		if cint(self.max_audio_mb) < 0:
			frappe.throw(_("Max Audio Size cannot be negative."), title=_("Invalid Audio Settings"))
		if cint(self.transcription_timeout) < 0:
			frappe.throw(_("Transcription Timeout cannot be negative."), title=_("Invalid Audio Settings"))
		self._require_enabled_model(self.transcription_model, _("Transcription Model"))
		self._require_enabled_model(self.speech_model, _("Speech Model"))

	@staticmethod
	def _require_enabled_model(model: str | None, label: str) -> None:
		if model and not frappe.db.get_value("AI Model", model, "enabled"):
			frappe.throw(_("{0} {1} is disabled.").format(label, model), title=_("Invalid Audio Settings"))
