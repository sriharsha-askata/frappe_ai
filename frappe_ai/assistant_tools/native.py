from __future__ import annotations

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from frappe_ai.tools import builtins


class _NativeTool(BaseTool):
	function = None
	name = ""
	description = ""
	inputSchema: dict[str, Any] = {"type": "object", "properties": {}}
	source_app = "frappe_ai"
	category = "read_write"

	def __init__(self):
		super().__init__()
		self.name = type(self).name
		self.description = type(self).description
		self.inputSchema = type(self).inputSchema
		self.source_app = "frappe_ai"

	def execute(self, arguments: dict[str, Any]) -> Any:
		return type(self).function(**arguments)


class ExecuteTool(_NativeTool):
	name = "execute"
	description = "Run permission-scoped Python in the hardened frappe_ai sandbox."
	function = staticmethod(builtins.execute)
	category = "privileged"
	inputSchema = {"type": "object", "required": ["code", "description"], "properties": {"code": {"type": "string"}, "description": {"type": "string"}}}


class RunActionTool(_NativeTool):
	name = "run_action"
	description = "Run a permitted document lifecycle action."
	function = staticmethod(builtins.run_action)
	category = "write"
	inputSchema = {"type": "object", "required": ["doctype", "names", "action"], "properties": {"doctype": {"type": "string"}, "names": {"type": "array", "items": {"type": "string"}}, "action": {"type": "string"}, "args": {"type": "object"}}}


class SearchKnowledgeTool(_NativeTool):
	name = "search_knowledge"
	description = "Search the configured knowledge bases for relevant passages."
	category = "read_only"
	inputSchema = {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}

	def execute(self, arguments):
		from frappe_ai.knowledge.retriever import retrieve
		return retrieve(arguments["query"], kbs=[])


class UpdateMemoryTool(_NativeTool):
	name = "update_memory"
	description = "Save a durable fact to agent memory."
	category = "write"
	inputSchema = {"type": "object", "required": ["content", "scope"], "properties": {"content": {"type": "string"}, "scope": {"type": "string"}, "memory_id": {"type": "string"}, "keywords": {"type": "string"}}}

	def execute(self, arguments):
		from frappe_ai.memory.memory import save_memory
		return save_memory(arguments.get("agent"), content=arguments["content"], scope=arguments["scope"], memory_id=arguments.get("memory_id"), keywords=arguments.get("keywords"))
