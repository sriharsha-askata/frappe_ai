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
