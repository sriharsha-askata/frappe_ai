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
		lancedb_path: DF.Data | None
		request_timeout: DF.Int
		search_type: DF.Literal["Hybrid", "Vector"]
		service_base_url: DF.Data | None
		service_status: DF.Data | None
		stream_timeout: DF.Int
	# end: auto-generated types

	def validate(self):
		self._validate_chunking()

	def _validate_chunking(self):
		if cint(self.chunk_size) < 1:
			frappe.throw(_("Chunk Size must be at least 1."), title=_("Invalid Chunking"))
		if cint(self.chunk_overlap) >= cint(self.chunk_size):
			frappe.throw(_("Chunk Overlap must be smaller than Chunk Size."), title=_("Invalid Chunking"))
