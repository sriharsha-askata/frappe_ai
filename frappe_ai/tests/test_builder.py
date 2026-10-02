# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from agno.models.message import Message

from frappe_ai.service.builder import AgentBuildError, AgentBuilder


class TestAgentBudgets(unittest.IsolatedAsyncioTestCase):
	"""`AI Agent.max_iterations` is documented as bounding the reasoning loop, but
	was never passed to Agno, leaving the loop unbounded."""

	def _client(self, agent_cfg):
		client = AsyncMock()
		client.get_run_config.return_value = {
			"agent": agent_cfg,
			"model": {
				"transport": "openai_compatible",
				"provider": "openai",
				"model_id": "custom-model",
				"api_key": "test",
				"base_url": "https://example.com/v1",
				"params": {},
			},
			"tools": [],
			"mcp_connections": [],
		}
		return client

	def _agent_cfg(self, **overrides):
		cfg = {
			"name": "Bounded Agent",
			"markdown": True,
			"reasoning": False,
			"max_iterations": 4,
		}
		cfg.update(overrides)
		return cfg

	async def test_max_iterations_becomes_agno_tool_call_limit(self):
		builder = AgentBuilder(frappe_client=self._client(self._agent_cfg()))

		agent, _ = await builder.build(run="RUN-1", user="Administrator")

		self.assertEqual(agent.tool_call_limit, 4)

	async def test_unset_max_iterations_leaves_no_limit(self):
		builder = AgentBuilder(frappe_client=self._client(self._agent_cfg(max_iterations=None)))

		agent, _ = await builder.build(run="RUN-1", user="Administrator")

		self.assertIsNone(agent.tool_call_limit)


class TestAgentBuilder(unittest.TestCase):
	def test_model_configuration_failure_has_normalized_code(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		with patch(
			"frappe_ai.service.builder.create_openai_compatible_model",
			side_effect=ValueError("invalid model parameters"),
		):
			with self.assertRaises(AgentBuildError) as context:
				builder._build_model({"provider": "google", "model_id": "bad-model", "params": {}})

		self.assertEqual(context.exception.code, "provider_error")

	def test_runtime_model_build_does_not_run_configuration_test_suite(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		with patch("frappe_ai.frappe_ai.doctype.ai_model.connection_test.run_capability_suite") as suite:
			with patch("frappe_ai.service.builder.create_openai_compatible_model"):
				builder._build_model(
					{
						"transport": "openai_compatible",
						"provider": "openai",
						"model_id": "runtime-model",
						"api_key": "test",
						"base_url": "https://example.com/v1",
						"params": {},
					}
				)

			suite.assert_not_called()

	def test_openai_compatible_model_preserves_system_role(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		model = builder._build_model(
			{
				"transport": "openai_compatible",
				"provider": "openai",
				"model_id": "custom-model",
				"api_key": "test",
				"base_url": "https://example.com/v1",
				"params": {},
			}
		)

		formatted = model._format_all_messages([Message(role="system", content="Be terse.")])

		self.assertEqual(formatted[0]["role"], "system")

	def _model_cfg(self, **overrides):
		cfg = {
			"transport": "openai_compatible",
			"provider": "openai",
			"model_id": "custom-model",
			"api_key": "test",
			"base_url": "https://example.com/v1",
			"params": {},
		}
		cfg.update(overrides)
		return cfg

	def test_agent_sampling_overrides_model_params(self):
		"""`AI Agent.temperature`/`top_p` were sent to the service and dropped:
		Agno carries sampling on the model, not the Agent."""
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		model = builder._build_model(
			self._model_cfg(params={"temperature": 0.9}),
			agent_cfg={"temperature": 0.1, "top_p": 0.5},
		)

		self.assertEqual(model.temperature, 0.1)
		self.assertEqual(model.top_p, 0.5)

	def test_reasoning_agent_sampling_is_not_forwarded(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		model = builder._build_model(
			self._model_cfg(params={}),
			agent_cfg={"temperature": 0.7, "top_p": 0.5, "reasoning": True},
		)

		self.assertIsNone(model.temperature)
		self.assertIsNone(model.top_p)

	def test_model_params_kept_when_agent_sampling_unset(self):
		builder = AgentBuilder(frappe_client=None)  # type: ignore[arg-type]

		model = builder._build_model(
			self._model_cfg(params={"temperature": 0.9}),
			agent_cfg={"temperature": None, "top_p": None},
		)

		self.assertEqual(model.temperature, 0.9)

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
