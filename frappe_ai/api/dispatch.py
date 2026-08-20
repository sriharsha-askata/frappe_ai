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

Confirmation (`requires_confirmation`) is decided by the service, not here: the
service already has each tool's flag from `get_agent_tools` and only calls dispatch
once a call is actually approved (or `auto_approve` is set). Dispatch always
executes what it's asked to execute — it is not a confirmation gate, only a
permission boundary.

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
def dispatch_tool(tool: str, user: str, arguments: dict | None = None, run: str | None = None) -> dict:
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

	tool_doc = frappe.get_doc("AI Tool", tool)
	if not tool_doc.enabled:
		frappe.throw(_("Tool {0} is disabled.").format(tool), title=_("Tool Disabled"))

	previous_user = frappe.session.user
	frappe.set_user(user)
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


@frappe.whitelist(allow_guest=True)
def dispatch_plugin_tool(tool: str, user: str, arguments: dict | None = None, run: str | None = None) -> dict:
	"""Execute a local Assistant Core tool under the run's acting user.

	This is deliberately separate from ``dispatch_tool`` while existing sites are
	migrated. It never falls back to AI Tool: registry availability, FAC
	configuration, role access, and the tool's own permission checks are all
	authoritative.
	"""
	_verify_service_secret()
	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} does not exist.").format(user), frappe.DoesNotExistError)

	previous_user = frappe.session.user
	frappe.set_user(user)
	try:
		from frappe_assistant_core.core.tool_registry import get_tool_registry
		from frappe_ai.api.budgets import consume

		consume(run, mutation=tool in {"create_document", "update_document", "delete_document", "run_workflow"}, records=_record_count(arguments or {}))
		result = get_tool_registry().execute_tool(tool, arguments or {})
		return {"result": result}
	except Exception as e:
		return {"error": _error_text(e)}
	finally:
		frappe.set_user(previous_user)


def _record_count(arguments: dict) -> int:
	for key in ("records", "documents", "values"):
		value = arguments.get(key)
		if isinstance(value, list):
			return max(len(value), 1)
	return 1


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
