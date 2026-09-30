# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Permission-enforcing tool dispatch — the endpoint ADR 0003 depends on.

`flow` has no equivalent to this module: its Agent runs in-process, so a tool call
is just a Python function call under whatever user the request handler already set.
`frappe_ai` splits orchestration into a separate FastAPI process, so every
Frappe-touching tool call must cross back over HTTP — this endpoint is that crossing
point, and it is the one place that must get ADR 0003's invariant right:

    A compromised or buggy service cannot exceed the permissions of the user on
    whose behalf it is acting.

Two things make that true here:

1. **Service-secret auth** (`_verify_service_secret`, identical to
   `api/service.py`'s) proves the *caller* is the FastAPI process, not an arbitrary
   client — the same `X-Frappe-AI-Service-Secret` header, never `Authorization`
   (see `frappe_client.py`'s module docstring for why that header is off-limits).
2. **`frappe.set_user(acting_user)` before anything else runs.** The service tells
   Frappe which user originated the call; every permission check inside the tool
   body (`frappe.has_permission`, `frappe.get_list`, `safe_exec`'s namespace) then
   runs as that user, not as whatever identity the shared secret would otherwise
   imply. This is the actual mechanism behind ADR 0003 — the secret authenticates
   the *process*, `set_user` scopes the *permissions*.

Every call must also name a live run owned by the acting user (`_require_active_run`),
so the secret alone cannot be used to act as an arbitrary user or to sidestep budgets.

Confirmation (`requires_confirmation`) is enforced here too, not only in the service
(`_enforce_confirmation`): a confirmation-required call runs only if the user's approval
for that exact call id, tool and argument digest is on record on the `AI Run` (written by
`resume_run`, single use), or the run was started with `auto_approve`. The service still
raises `PendingConfirmation` to pause the UI, but a compromised service can no longer skip it.

A tool that raises is caught and returned as `{"error": ...}` truncated to 500
chars, exactly as `flow`'s in-process tool calls behave — a failing tool must never
kill the run.
"""

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
	"""Execute one `AI Tool` call on behalf of `user`, enforcing that user's permissions.

	Args:
		tool (str): `AI Tool` slug (its `name`).
		user (str): The Frappe user originating this call — set via `frappe.set_user`
			before the tool body runs, so every permission check inside it is scoped
			to this user, not the service's own identity.
		arguments (dict | None): Keyword arguments to call the tool with.

	Returns:
		dict: `{"result": <json-serializable>}` on success, or `{"error": <message>}`
			(truncated to 500 chars) if the tool raised. Never raises itself except
			for the auth/lookup failures below — a failing *tool call* is reported in
			the response body, not as an HTTP error, so the service's run loop can
			feed it back to the model.

	Raises:
		frappe.AuthenticationError: If the service secret is missing or invalid.
		frappe.DoesNotExistError: If `tool` or `user` doesn't exist.
		frappe.ValidationError: If the tool is disabled.
	"""
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
	_enforce_confirmation(run, tool, call_id, arguments, _plugin_requires_confirmation(run, tool))

	previous_user = frappe.session.user
	previous_local_user = getattr(frappe.local, "user", None)
	frappe.set_user(user)
	frappe.local.user = user
	try:
		from frappe_assistant_core.core.tool_registry import get_tool_registry
		from frappe_ai.api.budgets import consume

		consume(run, mutation=tool in {"create_document", "update_document", "delete_document", "run_workflow"}, records=_record_count(arguments or {}))
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
			result = get_tool_registry().execute_tool(tool, tool_arguments)
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
	"""Bind a dispatch to a live run owned by the acting user.

	The shared secret authenticates the *process*; without this check anyone holding it
	could name any `user` and skip every per-run control (budgets, agent/KB scoping).
	A call is only honoured for a run that is still active and belongs to `user`.

	Raises:
		frappe.PermissionError: If `run` is missing, not active, or owned by someone else.
		frappe.DoesNotExistError: If `run` does not exist.
	"""
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
	"""Refuse a confirmation-required call unless the user approved exactly this call.

	Approval is recorded on the `AI Run` by the user-authenticated `resume_run` and is
	single-use (`consume_approval`), so the service cannot approve its own calls or replay
	an approved one. Runs started with `auto_approve` (agent/trigger setting) skip the check.

	Raises:
		frappe.PermissionError: If approval is required and not on record for this call.
	"""
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
	for key in ("records", "documents", "values"):
		value = arguments.get(key)
		if isinstance(value, list):
			return max(len(value), 1)
	return 1


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
	"""Verify the `X-Frappe-AI-Service-Secret` header against `site_config.json`.

	Identical check to `api/service.py`'s `_verify_service_secret` — duplicated
	rather than imported to keep this security-critical module self-contained and
	because `api/service.py` docstrings this pattern is deliberately about not
	using the `Authorization` header, which applies equally here.

	Raises:
		frappe.AuthenticationError: If the header is missing, the secret isn't
			configured, or it mismatches.
	"""
	provided = frappe.get_request_header("X-Frappe-AI-Service-Secret") or ""
	if not provided:
		frappe.throw(_("Missing service secret."), exc=frappe.AuthenticationError)

	expected = frappe.conf.get("frappe_ai_service_secret")
	if not expected or not hmac.compare_digest(provided, expected):
		frappe.throw(_("Invalid service secret."), exc=frappe.AuthenticationError)
