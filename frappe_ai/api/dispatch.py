# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Permission-enforcing tool dispatch for the FastAPI service (ADR 0003).

Authenticate the service secret, bind each call to an active run and acting user,
and enforce single-use approval for confirmation-gated calls. Tool exceptions
return bounded error results so they do not terminate the run."""

from __future__ import annotations

import hmac
from typing import Any

import frappe
from frappe import _

_ERROR_LIMIT = 500


@frappe.whitelist(allow_guest=True)
def dispatch_tool(
	tool: str, user: str, arguments: dict | None = None, run: str | None = None, call_id: str | None = None
) -> dict:
	"""Execute one `AI Tool` under the acting user's permissions.

	Return `{"result": ...}` or a tool-level `{"error": ...}` capped at 500
	characters. Authentication, lookup, and disabled-tool failures still raise."""
	_verify_service_secret()

	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} does not exist.").format(user), frappe.DoesNotExistError)
	_require_active_run(run, user)

	tool_doc = frappe.get_doc("AI Tool", tool)
	if not tool_doc.enabled:
		frappe.throw(_("Tool {0} is disabled.").format(tool), title=_("Tool Disabled"))
	_enforce_confirmation(run, tool, call_id, arguments, bool(tool_doc.requires_confirmation))

	previous_user = frappe.session.user
	previous_local_user = getattr(frappe.local, "user", None)
	frappe.set_user(user)
	frappe.local.user = user
	try:
		from frappe_ai.api.budgets import consume
		consume(run, mutation=tool in {"create", "update", "delete", "run_action"}, records=_record_count(arguments or {}))
		runtime_tool = tool_doc.to_tool()
		result = runtime_tool(**(arguments or {}))
		return {"result": result}
	except Exception as e:
		return {"error": _error_text(e)}
	finally:
		frappe.set_user(previous_user)
		frappe.local.user = previous_local_user


@frappe.whitelist(allow_guest=True)
def dispatch_plugin_tool(
	tool: str, user: str, arguments: dict | None = None, run: str | None = None, call_id: str | None = None
) -> dict:
	"""Execute a local Assistant Core tool under the run's acting user.

	This is deliberately separate from ``dispatch_tool`` while existing sites are
	migrated. It never falls back to AI Tool: registry availability, FAC
	configuration, role access, and the tool's own permission checks are all
	authoritative.
	"""
	_verify_service_secret()
	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} does not exist.").format(user), frappe.DoesNotExistError)
	_require_active_run(run, user)
	_require_plugin_binding(run, tool)
	_enforce_confirmation(run, tool, call_id, arguments, _plugin_requires_confirmation(run, tool))

	previous_user = frappe.session.user
	previous_local_user = getattr(frappe.local, "user", None)
	frappe.set_user(user)
	frappe.local.user = user
	try:
		from frappe_assistant_core.core.tool_registry import get_tool_registry
		from frappe_ai.api.budgets import consume

		registry = get_tool_registry()
		tool_instance = registry.get_tool(tool)
		if not tool_instance:
			frappe.throw(_("Tool {0} was not found in the Assistant Core registry.").format(tool), frappe.DoesNotExistError)
		mutation = _plugin_tool_is_mutating(tool, tool_instance)
		consume(
			run,
			mutation=mutation,
			records=_record_count(arguments or {}) if mutation else 1,
		)
		tool_arguments = dict(arguments or {})
		agent, model, knowledge_bases = _resolve_plugin_context(run)
		if tool == "search_knowledge":
			tool_arguments["__frappe_ai_knowledge_bases"] = knowledge_bases
		elif tool == "update_memory":
			if not agent:
				frappe.throw(
					_("update_memory requires an AI Run context."),
					title=_("Missing Run Context"),
				)
			# These values come from the persisted run graph, never from model-supplied
			# arguments. The native FAC wrapper consumes them without exposing them in
			# its public input schema.
			tool_arguments["__frappe_ai_agent"] = agent
			tool_arguments["__frappe_ai_source_run"] = run
		elif tool == "load_full_document_text":
			# The document-chunking budget must scale with the run's actual model
			# (see frappe_ai.lib.model.single_tool_result_char_budget), not a
			# hardcoded document-size constant blind to which model is running.
			tool_arguments["__frappe_ai_source_run"] = run
			tool_arguments["__frappe_ai_model"] = model

		try:
			result = registry.execute_tool(tool, tool_arguments)
		except Exception as e:
			# Log the exact arguments the model sent alongside the failure: a
			# tool's own ValidationError (e.g. "no matching file found") reads the
			# same in the model's error feedback whether the data was actually
			# missing or the model passed a wrong/malformed argument for it — this
			# is the only place both are visible together.
			frappe.log_error(
				title=f"frappe_ai dispatch_plugin_tool failed: {tool}",
				message=frappe.as_json(
					{"tool": tool, "user": user, "run": run, "arguments": tool_arguments, "error": _error_text(e)}
				),
			)
			return {"error": _error_text(e)}
		return {"result": result}
	finally:
		frappe.set_user(previous_user)
		frappe.local.user = previous_local_user


def _require_active_run(run: str | None, user: str) -> None:
	"""Require an active run owned by `user` before dispatch.

	The shared secret authenticates the service, while this check binds the call to
	its user, budget, and agent scope."""
	if not run:
		frappe.throw(_("A run is required to dispatch a tool call."), frappe.PermissionError)
	row = frappe.db.get_value("AI Run", run, ["status", "owner"], as_dict=True)
	if not row:
		frappe.throw(_("Run {0} was not found.").format(run), frappe.DoesNotExistError)
	if row.status not in ("Running", "Paused"):
		frappe.throw(_("Run {0} is not active (status: {1}).").format(run, row.status), frappe.PermissionError)
	if row.owner != user:
		frappe.throw(_("Run {0} does not belong to {1}.").format(run, user), frappe.PermissionError)


#: Tools whose scope (agent, knowledge bases, model) is injected server-side from the run
#: (`_resolve_plugin_context`), so the model cannot aim them anywhere. They may run even when
#: an agent has no explicit binding row for them.
_RUN_SCOPED_TOOLS = frozenset({"search_knowledge", "update_memory", "load_full_document_text"})


def _require_plugin_binding(run: str, tool: str) -> None:
	"""Require the run's agent to expose ``tool`` before considering approval.

	Binding controls whether an agent may use a tool; confirmation controls whether a
	particular call may proceed. Keeping these checks separate prevents ``auto_approve``
	from turning an unbound registry tool into an authorized one.
	"""
	if tool in _RUN_SCOPED_TOOLS:
		return
	run_doc = frappe.get_doc("AI Run", run)
	session_doc = frappe.get_doc("AI Session", run_doc.session)
	agent_doc = frappe.get_doc("AI Agent", session_doc.agent)
	if any(
		row.enabled and row.fac_tool == tool
		for row in agent_doc.get("plugin_tools") or []
	):
		return
	frappe.throw(
		_("Tool {0} is not enabled for agent {1}.").format(tool, agent_doc.name),
		frappe.PermissionError,
	)


def _plugin_requires_confirmation(run: str, tool: str) -> bool:
	"""Whether the run's agent requires approval for `tool`.

	The agent's own binding row is authoritative. A tool the agent has not bound at all is
	never one the model was offered, so it fails closed (approval required, which the
	service cannot supply) unless it is a run-scoped internal tool.
	"""
	run_doc = frappe.get_doc("AI Run", run)
	session_doc = frappe.get_doc("AI Session", run_doc.session)
	agent_doc = frappe.get_doc("AI Agent", session_doc.agent)
	rows = [row for row in agent_doc.get("plugin_tools") or [] if row.enabled and row.fac_tool == tool]
	if rows:
		return any(bool(row.requires_confirmation) for row in rows)
	return tool not in _RUN_SCOPED_TOOLS


def _enforce_confirmation(
	run: str, tool: str, call_id: str | None, arguments: dict | None, requires_confirmation: bool
) -> None:
	"""Require single-use approval for this exact tool call, unless auto-approved.

	The user-authenticated `resume_run` records approval; service calls cannot grant it."""
	if not requires_confirmation:
		return
	run_doc = frappe.get_doc("AI Run", run)
	snapshot = frappe.parse_json(run_doc.config_snapshot) if run_doc.config_snapshot else {}
	if snapshot.get("auto_approve"):
		return
	from frappe_ai.frappe_ai.doctype.ai_run.ai_run import consume_approval

	if consume_approval(run, call_id, tool, arguments):
		return
	frappe.throw(_("Tool {0} needs the user's approval for this call.").format(tool), frappe.PermissionError)


def _record_count(arguments: dict) -> int:
	list_sizes = [len(value) for value in arguments.values() if isinstance(value, list)]
	return max([1, *list_sizes])


def _plugin_tool_is_mutating(tool: str, tool_instance: Any) -> bool:
	"""Treat every plugin category except explicit ``read_only`` as mutating.

	Unknown and legacy categories fail safe: under-counting a write weakens the run
	budget, while counting an unknown read as a mutation only makes the budget more
	conservative.
	"""
	configured_category = frappe.db.get_value("FAC Tool Configuration", tool, "tool_category")
	category = str(configured_category or getattr(tool_instance, "category", "") or "").strip().lower()
	return category != "read_only"


def _resolve_plugin_context(run: str | None) -> tuple[str | None, str | None, list[str]]:
	"""Resolve server-owned agent/model scope for context-sensitive FAC tools.

	The model must never be able to choose the agent whose memories are written, the
	knowledge bases searched, or which AI Model a size budget is computed against.
	All three come from the persisted ``AI Run`` → ``AI Session`` → ``AI Agent`` graph,
	and the run owner check is performed while the acting user is installed.
	"""
	if not run:
		return None, None, []

	from frappe_ai.frappe_ai.doctype.ai_run.ai_run import assert_run_owner

	run_doc = frappe.get_doc("AI Run", run)
	assert_run_owner(run_doc)
	session_doc = frappe.get_doc("AI Session", run_doc.session)
	agent_doc = frappe.get_doc("AI Agent", session_doc.agent)
	model_name = session_doc.model or agent_doc.model
	knowledge_bases = [
		row.knowledge_base
		for row in getattr(agent_doc, "knowledge_bases", []) or []
		if getattr(row, "knowledge_base", None)
	]
	return agent_doc.name, model_name, knowledge_bases


def _error_text(e: Exception) -> str:
	"""Some frappe exceptions carry their message in the message log, not str() — fall
	back to the type. Truncated so one runaway tool error can't blow up the transcript."""
	return (str(e).strip() or e.__class__.__name__)[:_ERROR_LIMIT]


def _verify_service_secret() -> None:
	"""Authenticate the dedicated service-secret header or reject the call."""
	provided = frappe.get_request_header("X-Frappe-AI-Service-Secret") or ""
	if not provided:
		frappe.throw(_("Missing service secret."), exc=frappe.AuthenticationError)

	expected = frappe.conf.get("frappe_ai_service_secret")
	if not expected or not hmac.compare_digest(provided, expected):
		frappe.throw(_("Invalid service secret."), exc=frappe.AuthenticationError)
