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
		from frappe_ai.frappe_ai.doctype.ai_agent_plugin_tool.ai_agent_plugin_tool import AIAgentPluginTool
		from frappe_ai.frappe_ai.doctype.ai_agent_tool_config.ai_agent_tool_config import AIAgentToolConfig

		agent_type: DF.Literal["Agent", "Team"]
		enabled: DF.Check
		instructions: DF.LongText
		is_system_generated: DF.Check
		knowledge_bases: DF.TableMultiSelect[AIAgentKnowledgeBase]
		markdown: DF.Check
		max_iterations: DF.Int
		max_tool_calls: DF.Int
		max_mutations: DF.Int
		max_records_per_call: DF.Int
		max_runtime_seconds: DF.Int
		mcp_connections: DF.TableMultiSelect[AIAgentMCPConnection]
		model: DF.Link
		plugin_tools: DF.TableMultiSelect[AIAgentPluginTool]
		reasoning: DF.Check
		temperature: DF.Float
		title: DF.Data
		tools: DF.Table[AIAgentToolConfig]
		top_p: DF.Float
	# end: auto-generated types

	def validate(self):
		self._validate_max_iterations()
		self._ensure_knowledge_search_tool()
		self._populate_mcp_tools()
		validate_immutable(self)

	def _populate_mcp_tools(self):
		for row in getattr(self, "mcp_connections", []) or []:
			if not row.mcp_connection:
				continue
			mcp_doc = frappe.get_doc("AI MCP Connection", row.mcp_connection)
			tools = []
			tool_names = []
			for tool in getattr(mcp_doc, "tools", []) or []:
				tools.append({
					"tool_name": tool.tool_name,
					"description": tool.description or "",
					"available": tool.available
				})
				tool_names.append(tool.tool_name)
			row.available_tools = tools if tools else None
			row.tools_list = ", ".join(tool_names) if tool_names else ""


	def on_trash(self):
		block_delete(self, always=True)

	def before_rename(self, old: str, _new: str, _merge: bool = False) -> None:
		block_rename(self, old)

	def _validate_max_iterations(self):
		if self.max_iterations is not None and self.max_iterations < 1:
			frappe.throw(_("Max Iterations must be at least 1."), title=_("Invalid Max Iterations"))

	def _ensure_knowledge_search_tool(self):
		if not self.knowledge_bases:
			return
		if any(row.tool_name == KNOWLEDGE_SEARCH_SLUG for row in self.tools):
			return
		self.append("tools", {
			"tool_name": KNOWLEDGE_SEARCH_SLUG,
			"source": "manual",
			"description": "Search configured knowledge bases.",
			"enabled": 1,
		})

	def _snapshot(self, *, model: str | None = None) -> dict[str, Any]:
		return {
			"title": self.title,
			"model": model or self.model,
			"instructions": self.instructions,
			"tools": [],
			"mcp_connections": [row.mcp_connection for row in getattr(self, "mcp_connections", [])],
			"plugin_tools": [row.fac_tool for row in getattr(self, "plugin_tools", [])],
			"max_iterations": self.max_iterations or DEFAULT_MAX_ITERATIONS,
			"max_tool_calls": getattr(self, "max_tool_calls", None) or 50,
			"max_mutations": getattr(self, "max_mutations", None) or 20,
			"max_records_per_call": getattr(self, "max_records_per_call", None) or 100,
			"max_runtime_seconds": getattr(self, "max_runtime_seconds", None) or 600,
			"temperature": self.temperature,
			"top_p": self.top_p,
			"reasoning": bool(self.reasoning),
			"markdown": bool(self.markdown),
		}
