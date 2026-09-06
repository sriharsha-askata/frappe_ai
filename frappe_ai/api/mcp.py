# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import asyncio
import json
import shlex
import traceback
from typing import Any

import frappe
from frappe import _

CHECK_TIMEOUT_SECONDS = 5


@frappe.whitelist()
def check_connection(name: str) -> dict[str, Any]:
	doc = frappe.get_doc("AI MCP Connection", name)
	frappe.has_permission("AI MCP Connection", "read", doc.name, throw=True)
	result = asyncio.run(_check_connection_async(doc))
	_apply_result_to_doc(doc, result)
	return result


@frappe.whitelist()
def check_all_mcp_connections() -> list[dict[str, Any]]:
	rows = []
	for name in frappe.get_all("AI MCP Connection", filters={"enabled": 1}, pluck="name"):
		doc = frappe.get_doc("AI MCP Connection", name)
		result = asyncio.run(_check_connection_async(doc))
		_apply_result_to_doc(doc, result)
		rows.append({"name": doc.name, **result})
	return rows


@frappe.whitelist()
def get_mcp_health_dashboard() -> list[dict[str, Any]]:
	return frappe.get_all(
		"AI MCP Connection",
		fields=["name", "connection_name", "connection_type", "enabled", "is_connected", "last_check_time", "status_message"],
		order_by="modified desc",
	)


@frappe.whitelist()
def get_mcp_connection_tools(name: str, refresh: bool = False) -> list[dict[str, Any]]:
	"""Return the safe discovered tool catalog for an Agent form."""
	doc = frappe.get_doc("AI MCP Connection", name)
	frappe.has_permission("AI MCP Connection", "read", doc.name, throw=True)
	if refresh or not getattr(doc, "tools", None):
		result = check_connection(name)
		if not result.get("is_connected"):
			return []
		doc.reload()
	return [_tool_row(row) for row in getattr(doc, "tools", []) if row.available]


@frappe.whitelist()
def create_mcp_connection_from_json(json_config: str | dict[str, Any]) -> dict[str, Any]:
	if isinstance(json_config, str):
		try:
			json_config = json.loads(json_config)
		except (TypeError, ValueError) as e:
			frappe.throw(_("Invalid MCP JSON config: {0}").format(e), title=_("Invalid JSON"))
	if not isinstance(json_config, dict):
		frappe.throw(_("MCP config must be a JSON object."), title=_("Invalid JSON"))
	raw_config = json_config
	if "mcpServers" in json_config:
		servers = json_config.get("mcpServers")
		if not isinstance(servers, dict) or len(servers) != 1:
			frappe.throw(_("MCP config import requires exactly one server."), title=_("Invalid MCP Config"))
		connection_name, json_config = next(iter(servers.items()))
	else:
		connection_name = json_config.get("connection_name") or json_config.get("name")
	connection_type = json_config.get("connection_type") or json_config.get("transport") or "stdio"
	connection_type = {"sse": "SSE", "streamable-http": "streamable-http"}.get(connection_type, connection_type)
	command_args = json_config.get("args")
	if command_args is None:
		command_args = shlex.split(json_config.get("command") or "")

	doc = frappe.get_doc(
		{
			"doctype": "AI MCP Connection",
			"connection_name": connection_name,
			"connection_type": connection_type,
			"command": json_config.get("command"),
			"command_args": json.dumps(command_args),
			"endpoint_url": json_config.get("endpoint_url") or json_config.get("url"),
			"environment_variables": json.dumps(json_config.get("environment_variables") or json_config.get("env") or {}),
			"mcp_config": json.dumps(raw_config),
			"enabled": 1 if json_config.get("enabled", True) else 0,
		}
	).insert(ignore_permissions=True)
	return {"name": doc.name}


def _flatten_exception_group(exc: BaseException) -> list[BaseException]:
	"""Recursively unwrap BaseExceptionGroup / ExceptionGroup to its leaf causes.

	``str(ExceptionGroup(...))`` returns the unhelpful
	"unhandled errors in a TaskGroup (N sub-exceptions)" message and hides the
	actual root cause (e.g. ``httpx.ConnectError``). This helper walks the
	``exceptions`` tree so the caller can show the real error to the user and
	to ``frappe.log_error``.
	"""
	leaves: list[BaseException] = []
	stack: list[BaseException] = [exc]
	while stack:
		current = stack.pop()
		if isinstance(current, BaseExceptionGroup):
			stack.extend(current.exceptions)
			continue
		leaves.append(current)
	return leaves


def _format_failure(exc: BaseException) -> str:
	"""Return a compact, user-readable description of an exception, unwrapping
	any TaskGroup / ExceptionGroup so the actual cause is shown."""
	parts: list[str] = []
	seen: set[int] = set()
	for leaf in _flatten_exception_group(exc):
		if id(leaf) in seen:
			continue
		seen.add(id(leaf))
		name = type(leaf).__name__
		msg = str(leaf).strip() or repr(leaf)
		parts.append(f"{name}: {msg}" if msg else name)
	return "; ".join(parts) if parts else str(exc)


def _apply_result_to_doc(doc, result: dict[str, Any]) -> None:
	"""Persist a connection-check result onto ``doc`` correctly.

	``doc.save()`` (called by ``_sync_discovered_tools``) writes **all** in-memory
	fields back to the DB. If we wrote the new status with ``frappe.db.set_value``
	first, that subsequent ``doc.save()`` would overwrite the freshly-written
	``is_connected=1`` with the stale in-memory value (``0``). To avoid that, we
	update the in-memory doc fields first and then do a single ``doc.save()`` via
	``_sync_discovered_tools``. ``_update_status`` is kept as a safety net for the
	failure path where no ``doc.save()`` happens.
	"""
	doc.is_connected = 1 if result.get("is_connected") else 0
	doc.last_check_time = frappe.utils.now_datetime()
	doc.status_message = (result.get("status_message") or "")[:140]
	_update_status(doc.name, result)
	if result.get("is_connected"):
		_sync_discovered_tools(doc, result.get("tools") or [])


async def _check_connection_async(doc) -> dict[str, Any]:
	try:
		toolkit = _build_toolkit(doc)
	except BaseException as e:
		frappe.log_error(
			title=f"AI MCP Connection build failed: {doc.name}",
			message=traceback.format_exc(),
		)
		return {"is_connected": False, "status_message": _format_failure(e)}

	try:
		async with toolkit:
			await toolkit.initialize()
			if not getattr(toolkit, "initialized", False):
				return {
					"is_connected": False,
					"status_message": "Failed to initialize MCP toolkit.",
				}
			return {
				"is_connected": True,
				"status_message": f"Connected ({len(toolkit.functions)} tools)",
				"tools": _discover_tools(toolkit),
			}
	except BaseException as e:
		frappe.log_error(
			title=f"AI MCP Connection check failed: {doc.name}",
			message=traceback.format_exc(),
		)
		return {"is_connected": False, "status_message": _format_failure(e)}


def _build_toolkit(doc):
	try:
		from agno.tools.mcp import MCPTools
	except ImportError as e:
		raise RuntimeError("Agno MCP tools are unavailable: install the `mcp` package.") from e

	env = {}
	if doc.environment_variables:
		env = json.loads(doc.environment_variables) if isinstance(doc.environment_variables, str) else doc.environment_variables

	if doc.connection_type == "stdio":
		parts = shlex.split(doc.command or "")
		command_args = getattr(doc, "command_args", None)
		if command_args:
			args = json.loads(command_args) if isinstance(command_args, str) else command_args
		else:
			args = parts[1:]
		from mcp import StdioServerParameters

		return MCPTools(
			transport="stdio",
			server_params=StdioServerParameters(command=parts[0] if parts else doc.command, args=args or [], env=env),
			timeout_seconds=CHECK_TIMEOUT_SECONDS,
		)
	elif doc.connection_type == "streamable-http":
		from agno.tools.mcp.params import StreamableHTTPClientParams

		# Build auth headers for streamable-http transport
		headers = {}
		api_key = getattr(doc, "api_key", None)
		if api_key:
			api_secret = None
			# Try to get the password using Frappe's get_password method
			if hasattr(doc, "get_password"):
				api_secret = doc.get_password("api_secret", raise_exception=False)
			if not api_secret:
				api_secret = getattr(doc, "api_secret", None)
			if api_key and api_secret:
				headers["Authorization"] = f"token {api_key}:{api_secret}"

		return MCPTools(
			transport="streamable-http",
			server_params=StreamableHTTPClientParams(url=doc.endpoint_url, headers=headers),
			timeout_seconds=CHECK_TIMEOUT_SECONDS,
		)

	# Default to SSE for backward compatibility
	return MCPTools(url=doc.endpoint_url, env=env, transport="sse", timeout_seconds=CHECK_TIMEOUT_SECONDS)


def _discover_tools(toolkit) -> list[dict[str, Any]]:
	tools = []
	for name, function in (toolkit.functions or {}).items():
		tools.append(
			{
				"tool_name": name,
				"description": getattr(function, "description", "") or "",
				"input_schema": getattr(function, "parameters", {}) or {},
				"raw_metadata": {},
			}
		)
	return tools


def _sync_discovered_tools(doc, discovered: list[dict[str, Any]]) -> None:
	now = frappe.utils.now_datetime()
	by_name = {item["tool_name"]: item for item in discovered if item.get("tool_name")}
	for row in doc.tools or []:
		item = by_name.pop(row.tool_name, None)
		if item is None:
			row.available = 0
			continue
		_update_tool_row(row, item, now)
	for item in by_name.values():
		row = doc.append("tools", {"tool_name": item["tool_name"]})
		_update_tool_row(row, item, now)
	doc.populate_tools_list()
	doc.save(ignore_permissions=True)


def _update_tool_row(row, item: dict[str, Any], discovered_at) -> None:
	row.description = item.get("description") or ""
	row.input_schema = json.dumps(item.get("input_schema") or {})
	row.raw_metadata = json.dumps(item.get("raw_metadata") or {})
	row.available = 1
	row.last_discovered = discovered_at
	row.matched_ai_tool = _match_ai_tool(item["tool_name"])


def _match_ai_tool(tool_name: str) -> str | None:
	matches = frappe.get_all(
		"AI Tool",
		filters={"enabled": 1},
		or_filters=[{"name": tool_name}, {"slug": tool_name}, {"import_path": tool_name}],
		pluck="name",
	)
	return matches[0] if len(matches) == 1 else None


def _tool_row(row) -> dict[str, Any]:
	return {
		"name": row.tool_name,
		"description": row.description or "",
		"input_schema": json.loads(row.input_schema) if row.input_schema else {},
		"available": bool(row.available),
		"matched_ai_tool": row.matched_ai_tool,
		"last_discovered": row.last_discovered,
	}


def _update_status(name: str, result: dict[str, Any]) -> None:
	frappe.db.set_value(
		"AI MCP Connection",
		name,
		{
			"is_connected": 1 if result.get("is_connected") else 0,
			"last_check_time": frappe.utils.now_datetime(),
			"status_message": (result.get("status_message") or "")[:140],
		},
		update_modified=False,
	)
