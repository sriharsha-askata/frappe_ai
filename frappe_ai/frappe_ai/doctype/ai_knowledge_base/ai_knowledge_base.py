# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Knowledge Base` binds searchable content to agents.

An agent's knowledge-base binding authorizes ordinary KB content. Retrieval also
checks the acting user's read permission for chunks sourced from DocType records.
Do not bind a KB containing other restricted content to a broadly available agent.
"""

from __future__ import annotations

from frappe.model.document import Document

from frappe_ai.utils.system_generated import block_delete, block_rename, validate_immutable


class AIKnowledgeBase(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		enabled: DF.Check
		is_system_generated: DF.Check
		title: DF.Data
	# end: auto-generated types

	def validate(self):
		validate_immutable(self)

	def on_trash(self):
		block_delete(self)

	def before_rename(self, old: str, new: str, merge: bool = False) -> None:
		block_rename(self, old)
