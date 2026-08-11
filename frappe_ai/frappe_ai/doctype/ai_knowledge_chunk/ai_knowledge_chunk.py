# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Knowledge Chunk` — ported from `flow`'s `Flow Knowledge Chunk`
(see `apps/flow/flow/flow/doctype/flow_knowledge_chunk/flow_knowledge_chunk.py`).

**Naming is load-bearing**: `autoname: autoincrement` means `self.name` is a plain
integer, and that integer **is** the LanceDB `chunks` table's row `id` — see
`frappe_ai/knowledge/store.py`. MariaDB holds the authoritative chunk text; LanceDB
holds only vectors and FTS indexes keyed by this name. Changing the naming rule
silently breaks retrieval hydration.
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
