# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.model.document import Document


class AIMCPConnection(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Data | None
		api_secret: DF.Password | None
		command: DF.Data | None
		command_args: DF.JSON | None
		connection_name: DF.Data
		connection_type: DF.Literal["stdio", "SSE", "streamable-http"]
		enabled: DF.Check
		endpoint_url: DF.Data | None
		environment_variables: DF.JSON | None
		mcp_config: DF.JSON | None
		tools: DF.Table | None
		is_connected: DF.Check
		last_check_time: DF.Datetime | None
		status_message: DF.SmallText | None
	# end: auto-generated types

	def autoname(self):
		self.name = (self.connection_name or "").strip().lower().replace(" ", "-")

	def validate(self):
		self.connection_name = (self.connection_name or "").strip()
		self._normalize_mcp_config()
		if self.connection_type == "stdio":
			if not (self.command or "").strip():
				frappe.throw(_("Command is required for stdio connections."), title=_("Missing Command"))
			self.endpoint_url = None
		elif self.connection_type == "SSE":
			if not (self.endpoint_url or "").strip():
				frappe.throw(_("Endpoint URL is required for SSE connections."), title=_("Missing Endpoint"))
			self.command = None
		elif self.connection_type == "streamable-http":
			if not (self.endpoint_url or "").strip():
				frappe.throw(_("Endpoint URL is required for streamable-http connections."), title=_("Missing Endpoint"))
			self.command = None
		else:
			frappe.throw(_("Connection Type must be stdio, SSE, or streamable-http."), title=_("Invalid Connection Type"))
		if self.environment_variables:
			try:
				value = json.loads(self.environment_variables) if isinstance(self.environment_variables, str) else self.environment_variables
			except (TypeError, ValueError) as e:
				frappe.throw(_("Environment Variables must be valid JSON: {0}").format(e), title=_("Invalid JSON"))
			if not isinstance(value, dict):
				frappe.throw(_("Environment Variables must be a JSON object."), title=_("Invalid JSON"))

	def _normalize_mcp_config(self):
		if not getattr(self, "mcp_config", None):
			return
		try:
			config = json.loads(self.mcp_config) if isinstance(self.mcp_config, str) else self.mcp_config
		except (TypeError, ValueError) as e:
			frappe.throw(_("MCP Config must be valid JSON: {0}").format(e), title=_("Invalid JSON"))
		if not isinstance(config, dict):
			frappe.throw(_("MCP Config must be a JSON object."), title=_("Invalid JSON"))
		server = config.get("mcpServers", config)
		if "mcpServers" in config:
			if len(server) != 1:
				frappe.throw(_("MCP Config must contain exactly one server for a connection."), title=_("Invalid Config"))
			server = next(iter(server.values()))
		if not isinstance(server, dict):
			frappe.throw(_("MCP server configuration must be a JSON object."), title=_("Invalid Config"))
		transport = server.get("transport") or self.connection_type or "stdio"
		transport = {"sse": "SSE"}.get(transport, transport)
		if not self.connection_type:
			self.connection_type = transport
		if not self.command:
			self.command = server.get("command")
		if not self.endpoint_url:
			self.endpoint_url = server.get("url") or server.get("endpoint_url")
		if not getattr(self, "command_args", None) and server.get("args") is not None:
			self.command_args = json.dumps(server.get("args"))
		if not self.environment_variables and server.get("env") is not None:
			self.environment_variables = json.dumps(server.get("env"))

	def populate_tools_list(self):
		tool_names = []
		for tool in getattr(self, "tools", []) or []:
			if tool.tool_name:
				tool_names.append(tool.tool_name)
		self.tools_list = ", ".join(tool_names) if tool_names else ""

	def after_insert(self):
		self.populate_tools_list()

	def after_save(self):
		self.populate_tools_list()
