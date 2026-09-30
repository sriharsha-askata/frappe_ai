# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import re
import shlex
from typing import Any
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

# An executable is a single token. These characters only ever appear when a whole shell
# line was pasted into `command` (`python -m x && ...`); nothing here runs through a shell,
# so rejecting them turns a confusing runtime failure into a clear save-time error.
_FORBIDDEN_EXECUTABLE_CHARS = frozenset(";&|`$<>()\"'\\\n\r\t *?{}[]!#")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_json(value: Any, label: str) -> Any:
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError as e:
			frappe.throw(_("{0} must be valid JSON: {1}").format(label, e), title=_("Invalid JSON"))
	return value


def split_stdio_command(command: str | None, command_args: Any = None) -> tuple[str, list[str]]:
	"""Resolve a stdio connection to `(executable, args)`.

	Stored `command_args` win when present. Otherwise a legacy `command` holding a whole
	line (`python -m pkg`) is split with `shlex`. A `command_args` that merely repeats the
	whole split command (an old import path stored it that way) is treated as absent.

	Raises:
		ValueError: If `command` has unbalanced quotes or `command_args` isn't a list of strings.
	"""
	parts = shlex.split((command or "").strip())
	if not parts:
		return "", []
	if command_args in (None, ""):
		return parts[0], parts[1:]
	args = json.loads(command_args) if isinstance(command_args, str) else command_args
	if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
		raise ValueError("Command Arguments must be a JSON list of strings.")
	if args == parts:
		args = parts[1:]
	return parts[0], args


def is_masked(value: str | None) -> bool:
	return bool(value) and set(value) == {"*"}


class AIMCPConnection(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Password | None
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
			self._validate_stdio()
		elif self.connection_type in ("SSE", "streamable-http"):
			self._validate_remote()
		else:
			frappe.throw(_("Connection Type must be stdio, SSE, or streamable-http."), title=_("Invalid Connection Type"))
		self._validate_environment_variables()
		# `mcp_config` is import-only: it was folded into the structured fields above.
		# Keeping a second copy would let the two drift apart.
		self.mcp_config = None

	def _validate_stdio(self):
		if not (self.command or "").strip():
			frappe.throw(_("Command is required for stdio connections."), title=_("Missing Command"))
		try:
			executable, args = split_stdio_command(self.command, self.command_args)
		except ValueError as e:
			frappe.throw(_("Invalid stdio command: {0}").format(e), title=_("Invalid Command"))
		if bad := _FORBIDDEN_EXECUTABLE_CHARS.intersection(executable):
			frappe.throw(
				_("Command must be a single executable, without shell characters ({0}). Put arguments in Command Arguments.").format(
					" ".join(sorted(bad, key=repr)).strip() or _("whitespace")
				),
				title=_("Invalid Command"),
			)
		self.command = executable
		self.command_args = json.dumps(args)
		self.endpoint_url = None

	def _validate_remote(self):
		url = (self.endpoint_url or "").strip()
		if not url:
			frappe.throw(
				_("Endpoint URL is required for {0} connections.").format(self.connection_type),
				title=_("Missing Endpoint"),
			)
		parsed = urlparse(url)
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			frappe.throw(_("Endpoint URL must be a valid http(s) URL."), title=_("Invalid Endpoint"))
		self.endpoint_url = url
		self.command = None
		self.command_args = None

	def _validate_environment_variables(self):
		if not self.environment_variables:
			return
		value = _load_json(self.environment_variables, _("Environment Variables"))
		if not isinstance(value, dict):
			frappe.throw(_("Environment Variables must be a JSON object."), title=_("Invalid JSON"))
		for key, item in value.items():
			if not _ENV_NAME.match(str(key)):
				frappe.throw(_("Invalid environment variable name: {0}").format(key), title=_("Invalid JSON"))
			if not isinstance(item, str):
				frappe.throw(
					_("Environment variable {0} must be a string (quote the value).").format(key),
					title=_("Invalid JSON"),
				)
		self.environment_variables = json.dumps(value)

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
