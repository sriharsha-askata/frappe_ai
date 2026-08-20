# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import unittest
from unittest.mock import patch

from agno.models.message import Message

from frappe_ai.service.builder import AgentBuilder


class TestAgentBuilder(unittest.TestCase):
	def test_openai_compatible_model_preserves_system_role(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		model = builder._build_model(
			{
				"class_module": "agno.models.openai.chat",
				"class_name": "OpenAIChat",
				"model_id": "custom-model",
				"api_key": "test",
				"base_url": "https://example.com/v1",
				"params": {},
			}
		)

		formatted = model._format_all_messages([Message(role="system", content="Be terse.")])

		self.assertEqual(formatted[0]["role"], "system")

	def test_build_mcp_tools_passes_include_tools(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]
		connections = [
			{
				"name": "Tender MCP",
				"connection_type": "stdio",
				"command": "/home/a/harsha/harsha/env/bin/python -m tender_automation.tender_automation.ai.mcp_server",
				"environment_variables": {"A": "1"},
				"include_tools": ["extract_tender_documents"],
				"is_connected": True,
				"status_message": "ok",
			}
		]

		with patch("agno.tools.mcp.MCPTools") as mock_mcp_tools:
			result = builder._build_mcp_tools(connections)

		self.assertEqual(result, [mock_mcp_tools.return_value])
		kwargs = mock_mcp_tools.call_args.kwargs
		self.assertEqual(kwargs["transport"], "stdio")
		self.assertEqual(kwargs["include_tools"], ["extract_tender_documents"])
		self.assertEqual(kwargs["server_params"].command, connections[0]["command"])
		self.assertEqual(kwargs["server_params"].env, connections[0]["environment_variables"])
