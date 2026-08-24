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
		embedding_model: DF.Link | None
		lancedb_path: DF.Data | None
		request_timeout: DF.Int
		search_type: DF.Literal["Hybrid", "Vector"]
		service_base_url: DF.Data | None
		service_status: DF.Data | None
		stream_timeout: DF.Int
	# end: auto-generated types

	def validate(self):
		self._validate_chunking()
		self._guard_model_change()
		self._sync_embedding_dimension()

	def _validate_chunking(self):
		if cint(self.chunk_size) < 1:
			frappe.throw(_("Chunk Size must be at least 1."), title=_("Invalid Chunking"))
		if cint(self.chunk_overlap) >= cint(self.chunk_size):
			frappe.throw(_("Chunk Overlap must be smaller than Chunk Size."), title=_("Invalid Chunking"))

	def _guard_model_change(self):
		"""Existing vectors only make sense in the embedding space they were created in.
		Changing the model (even at the same dimension) while chunks exist is blocked;
		rebuild sets self.flags.allow_embedding_model_change to bypass."""
		if self.flags.allow_embedding_model_change:
			return
		previous = self.get_doc_before_save()
		if previous is None or previous.embedding_model == self.embedding_model:
			return
		# AI Knowledge Chunk doesn't exist until Phase 4; treat as no chunks until then.
		if not frappe.db.exists("DocType", "AI Knowledge Chunk"):
			return
		chunk_count = frappe.db.count("AI Knowledge Chunk")
		if chunk_count:
			frappe.throw(
				_(
					"{0} knowledge chunks were embedded with the current model. Delete all knowledge sources before changing the embedding model."
				).format(chunk_count),
				title=_("Embedding Model In Use"),
			)

	def _sync_embedding_dimension(self):
		if not self.embedding_model:
			self.embedding_dimension = 0
			return
		require_embedding_model(self.embedding_model)
		previous = self.get_doc_before_save()
		model_unchanged = previous is not None and previous.embedding_model == self.embedding_model
		if model_unchanged and self.embedding_dimension:
			return

		try:
			from frappe_ai.knowledge.embedder import probe_dimension
		except ImportError:
			# frappe_ai.knowledge.embedder doesn't exist until Phase 4; leave the
			# dimension unchanged until then.
			return

		self.embedding_dimension = probe_dimension(self.embedding_model)


def require_embedding_model(model_name: str | None = None):
	"""Require a configured AI Model explicitly marked for embeddings."""
	settings = frappe.get_cached_doc("AI Settings")
	model_name = model_name or settings.embedding_model
	if not model_name:
		frappe.throw(
			_("Set an embedding model in AI Settings before adding knowledge bases or sources."),
			title=_("AI Settings Required"),
		)
	model_type, model_enabled = frappe.db.get_value("AI Model", model_name, ["model_type", "enabled"])
	if model_type != "Embedding":
		frappe.throw(
			_("AI Model {0} is a Chat model. Select Model Type = Embedding before using it for knowledge.").format(
				model_name
			),
			title=_("Invalid Embedding Model"),
		)
	if not model_enabled:
		frappe.throw(_("AI Model {0} is disabled.").format(model_name), title=_("Model Disabled"))
