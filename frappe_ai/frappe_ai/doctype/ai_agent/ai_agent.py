# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Agent` — ported from `flow`'s `Flow Agent`
(see `apps/flow/flow/flow/doctype/flow_agent/flow_agent.py`).

Unlike `flow`, this controller does not `assemble()` a runtime agent or `run()` a
session — that's the FastAPI service's job now (`frappe_ai.service.builder.AgentBuilder`,
which runs in a different process and talks to Frappe over HTTP). This controller only
owns the config/validation surface: default tools, `_ensure_knowledge_search_tool`,
`_snapshot`, and the immutability/delete/rename guards.

`_resolve_tools()` (the flow name for turning agent rows into runtime tools) has no
equivalent here — `AgentBuilder.build()` does the resolving, in the service process,
by fetching this row's config plus each tool's schema from Frappe via the dispatch
endpoint rather than calling `to_tool()` locally.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_ai.utils.system_generated import block_delete, block_rename, validate_immutable

DEFAULT_TOOL_SLUGS = ("describe", "read", "execute")
DEFAULT_MAX_ITERATIONS = 10
KNOWLEDGE_SEARCH_SLUG = "search_knowledge"


class AIAgent(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from frappe_ai.frappe_ai.doctype.ai_agent_knowledge_base.ai_agent_knowledge_base import (
			AIAgentKnowledgeBase,
		)
		from frappe_ai.frappe_ai.doctype.ai_agent_mcp_connection.ai_agent_mcp_connection import (
			AIAgentMCPConnection,
		)
		from frappe_ai.frappe_ai.doctype.ai_agent_tool.ai_agent_tool import AIAgentTool

		agent_type: DF.Literal["Agent", "Team"]
		enabled: DF.Check
		instructions: DF.LongText
		is_system_generated: DF.Check
		knowledge_bases: DF.TableMultiSelect[AIAgentKnowledgeBase]
		markdown: DF.Check
		max_iterations: DF.Int
		mcp_connections: DF.TableMultiSelect[AIAgentMCPConnection]
		model: DF.Link
		reasoning: DF.Check
		temperature: DF.Float
		title: DF.Data
		tools: DF.TableMultiSelect[AIAgentTool]
		top_p: DF.Float
	# end: auto-generated types

	def before_insert(self):
		if not self.tools:
			for slug in DEFAULT_TOOL_SLUGS:
				if frappe.db.exists("AI Tool", slug):
					self.append("tools", {"tool": slug})

	def validate(self):
		self._validate_max_iterations()
		self._ensure_knowledge_search_tool()
		validate_immutable(self)

	def on_trash(self):
		block_delete(self, always=True)

	def before_rename(self, old: str, _new: str, _merge: bool = False) -> None:
		block_rename(self, old)

	def _validate_max_iterations(self):
		if self.max_iterations is not None and self.max_iterations < 1:
			frappe.throw(_("Max Iterations must be at least 1."), title=_("Invalid Max Iterations"))

	def _ensure_knowledge_search_tool(self):
		"""A bound knowledge base is inert without the search tool. Keep them consistent
		so any agent with knowledge bases can actually query them, however it was created."""
		if not self.knowledge_bases:
			return
		if any(row.tool == KNOWLEDGE_SEARCH_SLUG for row in self.tools):
			return
		if frappe.db.exists("AI Tool", KNOWLEDGE_SEARCH_SLUG):
			self.append("tools", {"tool": KNOWLEDGE_SEARCH_SLUG})

	def _snapshot(self, *, model: str | None = None) -> dict[str, Any]:
		"""Plain-dict config snapshot stored on every `AI Run.config_snapshot`."""
		return {
			"title": self.title,
			"model": model or self.model,
			"instructions": self.instructions,
			"tools": [row.tool for row in self.tools],
			"mcp_connections": [row.mcp_connection for row in getattr(self, "mcp_connections", [])],
			"max_iterations": self.max_iterations or DEFAULT_MAX_ITERATIONS,
			"temperature": self.temperature,
			"top_p": self.top_p,
			"reasoning": bool(self.reasoning),
			"markdown": bool(self.markdown),
		}
