# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Knowledge Chunk` stores indexed text and source provenance.

`autoname: autoincrement` makes `self.name` the integer ID of the chunk's row in the search
index (see `frappe_ai/knowledge/index.py`). The database owns chunk text and provenance;
the vector backend holds a derived, rebuildable copy for search. Changing the naming rule
breaks retrieval hydration.
"""

from __future__ import annotations

from frappe.model.document import Document


class AIKnowledgeChunk(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		chunk_index: DF.Int
		content: DF.LongText
		content_hash: DF.Data | None
		knowledge_base: DF.Link
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		source: DF.Link
	# end: auto-generated types

	pass
