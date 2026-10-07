# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Build an Agno agent from Frappe's run configuration.

The service fetches config over HTTP and keeps no local run state. Tools needing
approval raise `PendingConfirmation`; Frappe persists the questions for resume
rather than relying on Agno's session state (architecture §7.1)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agno.agent import Agent
from agno.tools.function import Function

from frappe_ai.lib.model import ModelConfigurationError, create_openai_compatible_model, normalize_provider_error
from frappe_ai.service.frappe_client import FrappeClient, FrappeClientError

logger = logging.getLogger("frappe_ai.service.builder")


class AgentBuildError(Exception):
	"""Raised when a run's config can't be turned into a runnable Agent."""

	def __init__(self, message: str, *, code: str | None = None):
		super().__init__(message)
		self.code = code


#: Prefix `PendingConfirmation.__str__` starts with. Agno's `Function.aexecute` catches
#: any plain `Exception` from an entrypoint and stores `str(e)` as the tool call's error
#: (see its own docstring for why this isn't `agno.exceptions.AgentRunException` — that
#: variant gets re-raised out of the whole run instead of being collected per-call). The
#: chat route greps completed tool-call events for this exact prefix to recover
#: `PendingConfirmation`s that Agno's error path only preserved as a string.
PENDING_CONFIRMATION_MARKER = "__frappe_ai_pending_confirmation__"


@dataclass
class PendingConfirmation(Exception):
	"""Signal that a tool call needs user approval before dispatch.

	Use a plain exception so Agno records each pending tool failure and the run loop
	can collect multiple confirmation questions in one turn."""

	tool_call_id: str
	name: str
	arguments: dict[str, Any]

	def __str__(self) -> str:
		return f"{PENDING_CONFIRMATION_MARKER}:{self.tool_call_id}:{self.name}:{json.dumps(self.arguments, default=str)}"


class AgentBuilder:
	"""Builds one turn's Agno `Agent` from a run's Frappe-fetched config.

	Attributes:
		frappe_client (FrappeClient): Used both to fetch config and, per-tool, to
			dispatch approved calls back to Frappe.
	"""

	def __init__(self, frappe_client: FrappeClient):
		self.frappe_client = frappe_client

	async def build(
		self,
		run: str,
		user: str,
		*,
		approved_call_ids: frozenset[str] = frozenset(),
	) -> tuple[Agent, dict[str, Any]]:
		"""Build an Agno agent and return it with the raw run configuration.

		Tool calls dispatch as `user`. `approved_call_ids` bypasses the pause gate for
		calls already approved on resume; it does not execute them by itself."""
		try:
			config = await self.frappe_client.get_run_config(run, user)
		except FrappeClientError as e:
			raise AgentBuildError(f"Could not fetch run config: {e}") from e

		agent_cfg = config["agent"]
		model = self._build_model(config["model"], agent_cfg=agent_cfg)
		tools = [
			self._build_tool(
				t,
				run=run,
				user=user,
				approved_call_ids=approved_call_ids,
				auto_approve=bool(agent_cfg.get("auto_approve")),
			)
			for t in config["tools"]
		]
		tools.extend(self._build_mcp_tools(config.get("mcp_connections") or []))

		agent = Agent(
			model=model,
			name=agent_cfg["name"],
			# Keep instructions in the transcript as a `system` message rather than
			# Agno's provider-specific `developer` role, which some OpenAI-compatible
			# backends reject.
			instructions=None,
			system_message_role="system",
			tools=tools,
			markdown=agent_cfg["markdown"],
			reasoning=agent_cfg["reasoning"],
			# Agno's own session/db features are unused — Frappe (AI Session/AI Run) is
			# the only place conversation state persists across requests.
			db=None,
			add_history_to_context=False,
			telemetry=False,
			# `AI Agent.max_iterations` bounds the reasoning loop. Agno spells this
			# as a cap on tool calls per run; without it the agent is free to loop
			# until the model stops asking for tools, and the field is inert.
			tool_call_limit=agent_cfg.get("max_iterations") or None,
		)
		return agent, config

	def _build_model(self, model_cfg: dict[str, Any], *, agent_cfg: dict[str, Any] | None = None):
		model_cfg = self._apply_agent_sampling(model_cfg, agent_cfg or {})
		try:
			return create_openai_compatible_model(model_cfg)
		except (ModelConfigurationError, KeyError, TypeError, ValueError) as e:
			error = normalize_provider_error(
				e,
				provider=model_cfg.get("provider"),
				model_id=model_cfg.get("model_id"),
			)
			raise AgentBuildError(error.message, code=error.code) from e

	@staticmethod
	def _apply_agent_sampling(model_cfg: dict[str, Any], agent_cfg: dict[str, Any]) -> dict[str, Any]:
		"""Apply agent sampling settings over model defaults.

		Reasoning agents omit `temperature` and `top_p`, which their models may reject."""
		if agent_cfg.get("reasoning"):
			return model_cfg
		overrides = {key: agent_cfg.get(key) for key in ("temperature", "top_p")}
		overrides = {key: value for key, value in overrides.items() if value is not None}
		if not overrides:
			return model_cfg
		merged = dict(model_cfg)
		merged["params"] = {**(merged.get("params") or {}), **overrides}
		return merged

	def _build_tool(
		self,
		tool_cfg: dict[str, Any],
		*,
		run: str,
		user: str,
		approved_call_ids: frozenset[str],
		auto_approve: bool,
	) -> Function:
		"""Build an Agno `Function` whose entrypoint dispatches to Frappe (ADR 0003),
		or raises `PendingConfirmation` when confirmation is required and not yet given.

		The function never executes anything locally — `frappe_ai.api.dispatch` is
		the only place these tools actually run, in Frappe, as `user`.
		"""
		name = tool_cfg["name"]
		if tool_cfg.get("source") != "fac":
			raise AgentBuildError(f"Unsupported local tool source for {name}: {tool_cfg.get('source')}")
		requires_confirmation = tool_cfg["requires_confirmation"]

		# `fc` is injected by Agno's `_build_entrypoint_args` when present in the
		# entrypoint's own signature (by name, not type) — it's the `FunctionCall`
		# instance for this specific invocation, whose `.call_id` is the stable id
		# `PendingConfirmation`/`approved_call_ids` key on. Declared as keyword-only
		# with a default so Agno's plain `entrypoint(**kwargs)` call path (when `fc`
		# isn't requested) still isn't required to pass it, per `_build_entrypoint_args`.
		async def entrypoint(*, fc: Any = None, **kwargs: Any) -> Any:
			call_id = getattr(fc, "call_id", None)
			if requires_confirmation and not auto_approve and call_id not in approved_call_ids:
				raise PendingConfirmation(tool_call_id=call_id or "", name=name, arguments=kwargs)

			response = await self.frappe_client.dispatch_plugin_tool(name, user, kwargs, run, call_id)
			if "error" in response:
				return {"error": response["error"]}
			return response.get("result")

		return Function(
			name=name,
			description=tool_cfg["description"],
			parameters=tool_cfg["parameters"],
			entrypoint=entrypoint,
			skip_entrypoint_processing=True,
		)

	def _build_mcp_tools(self, connections: list[dict[str, Any]]) -> list[Any]:
		try:
			from agno.tools.mcp import MCPTools
		except ImportError:
			if connections:
				logger.warning("MCP connections configured, but the `mcp` package is not installed; skipping them.")
			return []

		tools: list[Any] = []
		for connection in connections:
			if not connection.get("is_connected"):
				logger.warning(
					"Skipping MCP connection %s because it is marked disconnected: %s",
					connection.get("name"),
					connection.get("status_message"),
				)
				continue
			try:
				env = connection.get("environment_variables") or {}
				include_tools = connection.get("include_tools") or None
				connection_type = connection.get("connection_type")

				if connection_type == "stdio":
					from mcp import StdioServerParameters

					tools.append(
						MCPTools(
							transport="stdio",
							server_params=StdioServerParameters(
								command=connection.get("command"),
								args=connection.get("command_args") or [],
								env=env,
							),
							include_tools=include_tools,
						)
					)
				elif connection_type == "streamable-http":
					from agno.tools.mcp.params import StreamableHTTPClientParams

					# Build auth headers for streamable-http transport
					headers = {}
					api_key = connection.get("api_key")
					api_secret = connection.get("api_secret")
					if api_key and api_secret:
						headers["Authorization"] = f"token {api_key}:{api_secret}"

					tools.append(
						MCPTools(
							transport="streamable-http",
							server_params=StreamableHTTPClientParams(
								url=connection.get("endpoint_url"), headers=headers
							),
							include_tools=include_tools,
							timeout_seconds=300,
						)
					)
				else:
					# Default to SSE for backward compatibility
					tools.append(
						MCPTools(
							url=connection.get("endpoint_url"),
							env=env,
							transport="sse",
							include_tools=include_tools,
						)
					)
			except Exception as e:
				logger.warning("Skipping MCP connection %s: %s", connection.get("name"), e)
		return tools
