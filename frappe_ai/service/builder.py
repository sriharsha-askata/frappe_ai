# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Turn Frappe-fetched run config into a runnable `agno.agent.Agent`.

Replaces `flow`'s `FlowAgent.assemble()` — but runs in FastAPI, not Frappe, and
builds an Agno `Agent` instead of `flow.lib.agent.Agent`. Everything this needs
(agent fields, resolved model credentials, each bound tool's JSON Schema and
`requires_confirmation` flag, and the prompt messages) comes from one call to
`FrappeClient.get_run_config()` — `AgentBuilder` never talks to Frappe's database
directly (ADR 0003/`001-architecture.md` §4).

**Confirmation design — deliberately not Agno's native HITL.** Agno's `Function`
supports `requires_confirmation` natively, but its pause/resume
(`RunPausedEvent`/`Agent.continue_run`) is built around Agno's own session `db`
holding the paused `RunOutput` between the pause response and a later resume
request — a stateful dependency this architecture doesn't have (`frappe_ai` is
stateless between requests; Frappe is the only source of truth, per
`001-architecture.md` §7.1). Instead, confirmation is handled entirely in each
tool's `entrypoint`:

- A tool call not requiring confirmation (or already approved this turn, via
  `approved_call_ids`) dispatches immediately through `FrappeClient.dispatch_tool`.
- A tool call requiring confirmation and not yet approved raises
  `PendingConfirmation` instead of dispatching. The run loop
  (`frappe_ai.service.routes.chat`) catches these after each Agno turn and, if any
  occurred, treats the segment as Paused — mirroring `flow`'s own `Question`
  collection (`lib/agent.py`'s `_confirmation_question`), just implemented as a
  Python exception instead of a return value, since Agno's tool-calling loop
  doesn't have a "return a sentinel instead of a result" affordance.

This keeps Agno responsible for exactly what it's good at — the model-calling and
tool-invocation loop — while `AI Run`/`AI Session` in Frappe remain the only place
run state persists across requests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agno.agent import Agent
from agno.tools.function import Function

from frappe_ai.service.frappe_client import FrappeClient, FrappeClientError

logger = logging.getLogger("frappe_ai.service.builder")


class AgentBuildError(Exception):
	"""Raised when a run's config can't be turned into a runnable Agent."""


#: Prefix `PendingConfirmation.__str__` starts with. Agno's `Function.aexecute` catches
#: any plain `Exception` from an entrypoint and stores `str(e)` as the tool call's error
#: (see its own docstring for why this isn't `agno.exceptions.AgentRunException` — that
#: variant gets re-raised out of the whole run instead of being collected per-call). The
#: chat route greps completed tool-call events for this exact prefix to recover
#: `PendingConfirmation`s that Agno's error path only preserved as a string.
PENDING_CONFIRMATION_MARKER = "__frappe_ai_pending_confirmation__"


@dataclass
class PendingConfirmation(Exception):
	"""Raised by a tool's entrypoint instead of dispatching, when that call requires
	confirmation and hasn't been approved yet this turn. Caught by the run loop.

	Deliberately a plain `Exception`, not `agno.exceptions.AgentRunException` — the
	latter is re-raised by Agno out of the entire `arun()` generator on the first
	occurrence, which would stop the turn after just one pending call. A plain
	exception is instead swallowed by Agno into
	`ToolExecution(tool_call_error=True, ...)` with `str(self)` as the error text,
	letting multiple tool calls in the same turn each independently end up Paused —
	matching `flow`'s behaviour of collecting every pending `Question` in one pass.
	"""

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
		"""Fetch this run's config from Frappe and build a runnable Agent.

		Args:
			run (str): `AI Run` name.
			user (str): Acting user — every tool dispatch this Agent makes carries
				this identity, so `frappe.has_permission` on the Frappe side scopes
				to it (ADR 0003).
			approved_call_ids (frozenset[str]): Tool call ids that were already
				approved (via a resume's `answers`) and should dispatch immediately
				this turn rather than raising `PendingConfirmation` again. Empty on
				a fresh (non-resumed) turn.

		Returns:
			tuple[Agent, dict[str, Any]]: The built Agent, and the raw config dict
				(kept for `config_snapshot`/tool-metadata lookups the run loop needs,
				e.g. mapping a tool name back to its `requires_confirmation` flag).

		Raises:
			AgentBuildError: If Frappe rejects the run/config fetch, or the model
				class can't be instantiated.
		"""
		try:
			config = await self.frappe_client.get_run_config(run, user)
		except FrappeClientError as e:
			raise AgentBuildError(f"Could not fetch run config: {e}") from e

		agent_cfg = config["agent"]
		model = self._build_model(config["model"])
		tools = [
			self._build_tool(
				t,
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
		)
		return agent, config

	def _build_model(self, model_cfg: dict[str, Any]):
		"""Instantiate the Agno model class the same way `AIModel.test_connection()`
		does on the Frappe side (Phase 1) — provider-then-model credential precedence."""
		try:
			module = __import__(model_cfg["class_module"], fromlist=[model_cfg["class_name"]])
			model_cls = getattr(module, model_cfg["class_name"])
		except (ImportError, AttributeError, KeyError, TypeError) as e:
			raise AgentBuildError(f"Could not resolve Agno model class: {e}") from e

		kwargs: dict[str, Any] = {"id": model_cfg["model_id"]}
		if model_cfg.get("api_key"):
			kwargs["api_key"] = model_cfg["api_key"]
		if model_cfg.get("base_url"):
			kwargs["base_url"] = model_cfg["base_url"]
		if (
			model_cfg["class_module"].startswith("agno.models.openai")
			and model_cfg["class_name"] == "OpenAIChat"
			and "role_map" not in (model_cfg.get("params") or {})
		):
			kwargs["role_map"] = {
				"system": "system",
				"user": "user",
				"assistant": "assistant",
				"tool": "tool",
				"model": "assistant",
			}
		kwargs.update(model_cfg.get("params") or {})

		try:
			return model_cls(**kwargs)
		except Exception as e:
			raise AgentBuildError(f"Could not instantiate model {model_cfg.get('class_name')}: {e}") from e

	def _build_tool(
		self,
		tool_cfg: dict[str, Any],
		*,
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

			response = await self.frappe_client.dispatch_tool(name, user, kwargs)
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
				if connection.get("connection_type") == "stdio":
					tools.append(
						MCPTools(
							command=connection.get("command"),
							env=env,
							transport="stdio",
							include_tools=include_tools,
						)
					)
				else:
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
