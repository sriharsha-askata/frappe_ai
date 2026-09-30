# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.api import mcp
from frappe_ai.frappe_ai.doctype.ai_mcp_connection.ai_mcp_connection import split_stdio_command


class TestSplitStdioCommand(unittest.TestCase):
	def test_legacy_one_line_command_is_split(self):
		self.assertEqual(split_stdio_command("python -m pkg.server"), ("python", ["-m", "pkg.server"]))

	def test_stored_args_win_over_command_tail(self):
		self.assertEqual(split_stdio_command("python -m ignored", '["-m", "real"]'), ("python", ["-m", "real"]))

	def test_args_that_repeat_the_whole_command_are_dropped(self):
		self.assertEqual(
			split_stdio_command("python -m pkg", '["python", "-m", "pkg"]'), ("python", ["-m", "pkg"])
		)

	def test_empty_command(self):
		self.assertEqual(split_stdio_command("  "), ("", []))

	def test_args_must_be_a_list_of_strings(self):
		for bad in ('{"a": 1}', "[1, 2]", '"x"'):
			with self.assertRaises(ValueError):
				split_stdio_command("python", bad)

	def test_unbalanced_quotes_raise(self):
		with self.assertRaises(ValueError):
			split_stdio_command("python 'unterminated")


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
		self.assertEqual(doc.command, "python")
		self.assertEqual(doc.command_args, "[\"-m\", \"example_mcp\"]")
		self.assertFalse(doc.mcp_config)

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


class TestMCPConnectionValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _connection(self, **fields):
		return frappe.get_doc(
			{"doctype": "AI MCP Connection", "connection_name": "Validation MCP", "connection_type": "stdio", **fields}
		)

	def test_legacy_command_is_split_on_save(self):
		doc = self._connection(command="python -m example_mcp").insert(ignore_permissions=True)

		self.assertEqual(doc.command, "python")
		self.assertEqual(doc.command_args, '["-m", "example_mcp"]')

	def test_shell_characters_in_executable_rejected(self):
		for command in ("python; rm -rf /", "python && ls", "$(whoami)", "a|b"):
			with self.assertRaises(frappe.ValidationError, msg=command):
				self._connection(command=command, command_args="[]").insert(ignore_permissions=True)

	def test_command_args_must_be_string_list(self):
		with self.assertRaises(frappe.ValidationError):
			self._connection(command="python", command_args='{"a": 1}').insert(ignore_permissions=True)

	def test_environment_values_must_be_strings(self):
		with self.assertRaises(frappe.ValidationError):
			self._connection(command="python", environment_variables='{"PORT": 8080}').insert(
				ignore_permissions=True
			)

	def test_environment_names_must_be_valid(self):
		with self.assertRaises(frappe.ValidationError):
			self._connection(command="python", environment_variables='{"BAD NAME": "x"}').insert(
				ignore_permissions=True
			)

	def test_remote_endpoint_must_be_http_url(self):
		for url in ("ftp://example.com", "not a url", "example.com/mcp"):
			with self.assertRaises(frappe.ValidationError, msg=url):
				self._connection(connection_type="SSE", endpoint_url=url).insert(ignore_permissions=True)

	def test_remote_connection_drops_stdio_fields(self):
		doc = self._connection(
			connection_type="streamable-http",
			endpoint_url="https://example.com/mcp",
			command="python",
			command_args='["-m", "x"]',
		).insert(ignore_permissions=True)

		self.assertFalse(doc.command)
		self.assertFalse(doc.command_args)

	def test_mcp_config_is_folded_into_fields_then_cleared(self):
		doc = self._connection(
			mcp_config='{"mcpServers": {"x": {"command": "python", "args": ["-m", "srv"], "env": {"K": "v"}}}}',
			command=None,
		).insert(ignore_permissions=True)

		self.assertEqual(doc.command, "python")
		self.assertEqual(doc.command_args, '["-m", "srv"]')
		self.assertEqual(doc.environment_variables, '{"K": "v"}')
		self.assertFalse(doc.mcp_config)

	def test_import_endpoint_requires_create_permission(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			mcp.create_mcp_connection_from_json({"name": "Nope", "command": "python"})

	def test_check_all_requires_system_manager(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			mcp.check_all_mcp_connections()
