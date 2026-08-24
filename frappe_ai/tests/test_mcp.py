# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from unittest.mock import patch

from frappe_ai.api import mcp


class TestMCP(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_create_connection_from_json(self):
		result = mcp.create_mcp_connection_from_json(
			{
				"name": "Local MCP",
				"transport": "stdio",
				"command": "python -m example_mcp",
				"env": {"TOKEN": "x"},
			}
		)
		doc = frappe.get_doc("AI MCP Connection", result["name"])
		self.assertEqual(doc.connection_type, "stdio")
		self.assertIn("TOKEN", doc.environment_variables or "")
		self.assertEqual(doc.command_args, "[\"-m\", \"example_mcp\"]")
		self.assertTrue(doc.mcp_config)

	def test_create_connection_from_standard_mcp_config(self):
		result = mcp.create_mcp_connection_from_json(
			{
				"mcpServers": {
					"Standard MCP": {
						"command": "python",
						"args": ["-m", "example_mcp"],
						"env": {"TOKEN": "x"},
					}
				}
			}
		)
		doc = frappe.get_doc("AI MCP Connection", result["name"])
		self.assertEqual(doc.name, "standard-mcp")
		self.assertEqual(doc.command_args, "[\"-m\", \"example_mcp\"]")

	def test_check_connection_discovers_and_syncs_tools(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI MCP Connection",
				"connection_name": "Discovery MCP",
				"connection_type": "stdio",
				"command": "python",
			}
		).insert(ignore_permissions=True)

		class Function:
			def __init__(self, name, description, parameters):
				self.name = name
				self.description = description
				self.parameters = parameters

		class InitializedToolkit:
			initialized = False
			functions = {
				"remote_search": Function(
					"remote_search",
					"Search the remote system.",
					{"type": "object", "properties": {"query": {"type": "string"}}},
				)
			}

			async def __aenter__(self):
				return self

			async def __aexit__(self, exc_type, exc_val, exc_tb):
				return None

			async def initialize(self):
				self.initialized = True

		with patch("frappe_ai.api.mcp._build_toolkit", return_value=InitializedToolkit()):
			result = mcp.check_connection(doc.name)

		self.assertTrue(result["is_connected"])
		doc.reload()
		self.assertEqual(len(doc.tools), 1)
		self.assertEqual(doc.tools[0].tool_name, "remote_search")
		self.assertEqual(doc.tools[0].description, "Search the remote system.")
		self.assertEqual(doc.tools[0].available, 1)

	def test_get_connection_tools_returns_available_catalog(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI MCP Connection",
				"connection_name": "Catalog MCP",
				"connection_type": "stdio",
				"command": "python",
				"tools": [
					{
						"doctype": "AI MCP Tool",
						"tool_name": "visible_tool",
						"description": "Visible",
						"available": 1,
					},
					{
						"doctype": "AI MCP Tool",
						"tool_name": "missing_tool",
						"available": 0,
					},
				],
			}
		).insert(ignore_permissions=True)
		tools = mcp.get_mcp_connection_tools(doc.name)
		self.assertEqual([tool["name"] for tool in tools], ["visible_tool"])

	def test_check_connection_fails_cleanly_without_mcp_dependency(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI MCP Connection",
				"connection_name": "Missing Dependency MCP",
				"connection_type": "stdio",
				"command": "python -m example_mcp",
			}
		).insert(ignore_permissions=True)
		with patch(
			"frappe_ai.api.mcp._build_toolkit",
			side_effect=RuntimeError("Agno MCP tools are unavailable: install the `mcp` package."),
		):
			result = mcp.check_connection(doc.name)
		self.assertFalse(result["is_connected"])
		self.assertIn("mcp", result["status_message"].lower())

	def test_check_connection_fails_when_toolkit_does_not_initialize(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI MCP Connection",
				"connection_name": "Uninitialized MCP",
				"connection_type": "stdio",
				"command": "python -m example_mcp",
			}
		).insert(ignore_permissions=True)

		class UninitializedToolkit:
			functions = {}
			initialized = False

			async def __aenter__(self):
				return self

			async def __aexit__(self, exc_type, exc_val, exc_tb):
				return None

			async def initialize(self):
				return None

		with patch("frappe_ai.api.mcp._build_toolkit", return_value=UninitializedToolkit()):
			result = mcp.check_connection(doc.name)

		self.assertFalse(result["is_connected"])
		self.assertIn("initialize", result["status_message"].lower())
