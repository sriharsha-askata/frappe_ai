# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import asyncio
import json
from typing import Any

import frappe
from frappe import _

CHECK_TIMEOUT_SECONDS = 5


@frappe.whitelist()
def check_connection(name: str) -> dict[str, Any]:
	doc = frappe.get_doc("AI MCP Connection", name)
	frappe.has_permission("AI MCP Connection", "read", doc.name, throw=True)
	result = asyncio.run(_check_connection_async(doc))
	_update_status(doc.name, result)
	return result


@frappe.whitelist()
def check_all_mcp_connections() -> list[dict[str, Any]]:
	rows = []
	for name in frappe.get_all("AI MCP Connection", filters={"enabled": 1}, pluck="name"):
		doc = frappe.get_doc("AI MCP Connection", name)
		result = asyncio.run(_check_connection_async(doc))
		_update_status(doc.name, result)
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
def create_mcp_connection_from_json(json_config: str | dict[str, Any]) -> dict[str, Any]:
	if isinstance(json_config, str):
		try:
			json_config = json.loads(json_config)
		except (TypeError, ValueError) as e:
			frappe.throw(_("Invalid MCP JSON config: {0}").format(e), title=_("Invalid JSON"))
	if not isinstance(json_config, dict):
		frappe.throw(_("MCP config must be a JSON object."), title=_("Invalid JSON"))

	doc = frappe.get_doc(
		{
			"doctype": "AI MCP Connection",
			"connection_name": json_config.get("connection_name") or json_config.get("name"),
			"connection_type": json_config.get("connection_type") or json_config.get("transport") or "stdio",
			"command": json_config.get("command"),
			"endpoint_url": json_config.get("endpoint_url") or json_config.get("url"),
			"environment_variables": json.dumps(json_config.get("environment_variables") or json_config.get("env") or {}),
			"enabled": 1 if json_config.get("enabled", True) else 0,
		}
	).insert(ignore_permissions=True)
	return {"name": doc.name}


async def _check_connection_async(doc) -> dict[str, Any]:
	try:
		toolkit = _build_toolkit(doc)
	except Exception as e:
		return {"is_connected": False, "status_message": str(e)}

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
			}
	except Exception as e:
		return {"is_connected": False, "status_message": str(e)}


def _build_toolkit(doc):
	try:
		from agno.tools.mcp import MCPTools
	except ImportError as e:
		raise RuntimeError("Agno MCP tools are unavailable: install the `mcp` package.") from e

	env = {}
	if doc.environment_variables:
		env = json.loads(doc.environment_variables) if isinstance(doc.environment_variables, str) else doc.environment_variables
	if doc.connection_type == "stdio":
		return MCPTools(command=doc.command, env=env, transport="stdio", timeout_seconds=CHECK_TIMEOUT_SECONDS)
	return MCPTools(url=doc.endpoint_url, env=env, transport="sse", timeout_seconds=CHECK_TIMEOUT_SECONDS)


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
