# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Frappe endpoints for service authentication and run configuration.

The service uses a shared secret from site config, not a Frappe login session.
`get_run_config` supplies the agent, model, tools, and prompt under the acting
user's permissions; `mint_token` binds a run token to its run and session."""

from __future__ import annotations

import hmac
import json
import shlex

import requests

import frappe
from frappe import _
from frappe.utils import cint

from frappe_ai.api._service_url import get_service_url
from frappe_ai.lib.model import ModelConfigurationError, get_default_model, resolve_model_config
from frappe_ai.service.auth import DEFAULT_TTL_SECONDS, mint_run_token

SERVICE_HEALTH_TIMEOUT = 5


@frappe.whitelist()
def mint_token(run: str, session: str, ttl_seconds: int | None = None) -> dict:
	"""Mint a token for a Running run owned by the caller and bound to `session`.

	Return `{"success": True, "data": {"token": str, "expires_in": int}}`."""
	from frappe_ai.frappe_ai.doctype.ai_run.ai_run import assert_run_owner

	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Login required."), exc=frappe.AuthenticationError)

	run_doc = frappe.get_doc("AI Run", run)
	if run_doc.session != session:
		frappe.throw(_("Run {0} does not belong to session {1}.").format(run, session))
	assert_run_owner(run_doc)
	if run_doc.status != "Running":
		frappe.throw(_("Run {0} is not Running (status: {1}).").format(run, run_doc.status))

	ttl = cint(ttl_seconds) or DEFAULT_TTL_SECONDS
	secret = frappe.conf.get("frappe_ai_service_secret")
	if not secret:
		frappe.throw(
			_("frappe_ai_service_secret is not set in site_config.json."), title=_("Service Not Configured")
		)

	token = mint_run_token(run=run, session=session, user=user, secret=secret, ttl_seconds=ttl)

	return {
		"success": True,
		"message": _("Token minted"),
		"data": {"token": token, "expires_in": ttl},
	}


@frappe.whitelist()
def service_health() -> dict:
	"""Check whether the FastAPI service is up by calling its `GET /health`.

	Returns:
		dict: `{"success": True, "data": {...health payload...}}` on a reachable
			service, or `{"success": False, "message": ...}` if unreachable.
	"""
	base_url = get_service_url()
	if not base_url:
		return {
			"success": False,
			"message": _("frappe_ai_service_url is not configured."),
			"data": {},
		}

	try:
		response = requests.get(f"{base_url.rstrip('/')}/health", timeout=SERVICE_HEALTH_TIMEOUT)
		response.raise_for_status()
		return {"success": True, "message": _("Service reachable"), "data": response.json()}
	except requests.RequestException as e:
		frappe.log_error(
			title="AI Service Health Check Error",
			message=f"Failed to reach FastAPI service at {base_url}: {e}\n{frappe.get_traceback()}",
		)
		return {"success": False, "message": str(e), "data": {}}


@frappe.whitelist(allow_guest=True)
def get_service_config() -> dict:
	"""Return service settings after validating the shared-secret header.

	`allow_guest=True` permits header authentication without a Frappe login;
	`_verify_service_secret` enforces access."""
	_verify_service_secret()

	settings = frappe.get_cached_doc("AI Settings")
	return {
		"request_timeout": settings.request_timeout,
		"stream_timeout": settings.stream_timeout,
		"lancedb_path": settings.lancedb_path,
	}


@frappe.whitelist(allow_guest=True)
def get_run_config(run: str, user: str) -> dict:
	"""Return the agent, model, tools, prompt, snapshot, and pending questions for a run.

	Authenticate the service and recheck the active run under `user` before reading
	configuration. The payload keys are `agent`, `model`, `tools`, `mcp_connections`,
	`messages`, `config_snapshot`, and `questions`; approved pending calls use the
	stored question arguments on resume."""
	_verify_service_secret()
	original_user = frappe.session.user
	original_local_user = getattr(frappe.local, "user", None)
	original_ignore_permissions = getattr(frappe.flags, "ignore_permissions", False)
	frappe.set_user(user)
	# Some Frappe request paths consult local.user while others consult
	# session.user. Keep both identities aligned for service callbacks.
	frappe.local.user = user
	# Whitelisted guest callbacks cannot reliably load owner-scoped documents even
	# after set_user(). Ownership is checked explicitly below; ignore_permissions is
	# limited to loading this already-bound run/session/agent graph.
	frappe.flags.ignore_permissions = True
	try:
		run_doc, session_doc, agent_doc, model_doc = _load_run_graph(run, user)
		return _run_config_payload(run_doc, session_doc, agent_doc, model_doc, user)
	finally:
		frappe.set_user(original_user)
		frappe.local.user = original_local_user
		frappe.flags.ignore_permissions = original_ignore_permissions


def _load_run_graph(run: str, user: str):
	"""Load the active run, owned session, agent, and usable model in that order."""
	run_row = frappe.db.get_value("AI Run", run, "*", as_dict=True)
	if not run_row:
		frappe.throw(_("Run {0} was not found.").format(run), frappe.DoesNotExistError)
	run_doc = frappe.get_doc({"doctype": "AI Run", **run_row})
	if run_doc.status != "Running" and run_doc.status != "Paused":
		frappe.throw(_("Run {0} is not active (status: {1}).").format(run, run_doc.status))

	session_doc = frappe.get_doc("AI Session", run_doc.session)
	if session_doc.owner != user:
		frappe.throw(_("Run {0} does not belong to {1}.").format(run, user), frappe.PermissionError)

	agent_doc = frappe.get_doc("AI Agent", session_doc.agent)
	model_name = session_doc.model or agent_doc.model
	if not frappe.has_permission("AI Model", "read", model_name):
		frappe.throw(_("{0} is not permitted to use AI Model {1}.").format(user, model_name), frappe.PermissionError)
	model_doc = frappe.get_doc("AI Model", model_name)
	if not model_doc.enabled:
		frappe.throw(_("AI Model {0} is disabled.").format(model_name), title=_("Disabled Model"))
	return run_doc, session_doc, agent_doc, model_doc


def _run_config_payload(run_doc, session_doc, agent_doc, model_doc, user: str) -> dict:
	"""Assemble the payload consumed by the service's AgentBuilder."""
	plugin_tools = _resolve_agent_plugin_tools(agent_doc, user)
	return {
		"agent": {
			"name": agent_doc.name,
			"instructions": agent_doc.instructions,
			"max_iterations": agent_doc.max_iterations,
			"temperature": agent_doc.temperature,
			"top_p": agent_doc.top_p,
			"reasoning": bool(agent_doc.reasoning),
			"markdown": bool(agent_doc.markdown),
			"auto_approve": bool(
				(json.loads(run_doc.config_snapshot) if run_doc.config_snapshot else {}).get("auto_approve")
			),
		},
		"model": _model_call_config(model_doc),
		"tools": plugin_tools,
		"mcp_connections": _resolve_agent_mcp_connections(
			agent_doc, direct_tool_names={tool["name"] for tool in plugin_tools}
		),
		"messages": session_doc.build_prompt_messages(),
		"config_snapshot": json.loads(run_doc.config_snapshot) if run_doc.config_snapshot else {},
		# Pending confirmations from the prior segment, `[{key, name, arguments,
		# prompt}, ...]` — the arguments a resume's approved calls must actually be
		# dispatched with. Empty on a fresh (non-resumed) run.
		"questions": json.loads(run_doc.questions) if run_doc.questions else [],
	}


@frappe.whitelist(allow_guest=True)
def get_fallback_model_config(user: str) -> dict:
	"""Return the enabled default model for a provider-error retry.

	Apply shared-secret authentication and `user`'s model-read permission;
	raise if no eligible default exists."""
	_verify_service_secret()
	original_user = frappe.session.user
	original_local_user = getattr(frappe.local, "user", None)
	frappe.set_user(user)
	frappe.local.user = user
	try:
		model_name = get_default_model()
		if not model_name:
			frappe.throw(_("No enabled default AI Model is configured."), frappe.DoesNotExistError)
		if not frappe.has_permission("AI Model", "read", model_name):
			frappe.throw(
				_("{0} is not permitted to use AI Model {1}.").format(user, model_name), frappe.PermissionError
			)
		model_doc = frappe.get_doc("AI Model", model_name)
		if not model_doc.enabled:
			frappe.throw(_("AI Model {0} is disabled.").format(model_name), title=_("Disabled Model"))
		return _model_call_config(model_doc)
	finally:
		frappe.set_user(original_user)
		frappe.local.user = original_local_user


def _model_call_config(model_doc) -> dict:
	"""Resolve an ``AI Model`` into the shared OpenAI-compatible transport config."""
	try:
		config = resolve_model_config(model_doc)
	except ModelConfigurationError as exc:
		frappe.throw(str(exc), title=_("Invalid Model Configuration"))
	try:
		stream_timeout = frappe.get_cached_value("AI Settings", "AI Settings", "stream_timeout") or 600
	except Exception:
		stream_timeout = 600
	params = dict(config.get("params") or {})
	params.setdefault("timeout", int(stream_timeout))
	config["params"] = params
	return config


def _resolve_agent_plugin_tools(agent_doc, user: str) -> list[dict]:
	"""Resolve direct local bindings from Assistant Core's authoritative registry.

	The legacy AI Tool and AI FAC Tool tables are intentionally not consulted here.
	A binding is exposed only when the registry says the tool is enabled and
	accessible for the acting user; this prevents disabled or role-restricted tools
	from leaking into the model schema.
	"""
	try:
		from frappe_assistant_core.core.tool_registry import get_tool_registry
	except ImportError:
		return []

	available = {
		item.get("name"): item
		for item in get_tool_registry().get_available_tools(user=user)
		if item.get("name")
	}
	resolved: list[dict] = []
	for row in getattr(agent_doc, "plugin_tools", []) or []:
		if not row.enabled or not row.fac_tool:
			continue
		tool_name = row.fac_tool
		item = available.get(tool_name)
		if not item:
			continue
		resolved.append(
			{
				"name": tool_name,
				"description": item.get("description", ""),
				"parameters": item.get("inputSchema") or item.get("parameters") or {},
				"requires_confirmation": bool(row.requires_confirmation),
				"source": "fac",
				"category": item.get("category"),
				"source_app": item.get("source_app"),
			}
		)
	return resolved


def _resolve_agent_mcp_connections(agent_doc, direct_tool_names: set[str] | None = None) -> list[dict]:
	direct_tool_names = direct_tool_names or set()
	resolved: list[dict] = []
	for row in getattr(agent_doc, "mcp_connections", []) or []:
		try:
			doc = frappe.get_doc("AI MCP Connection", row.mcp_connection)
		except frappe.DoesNotExistError:
			frappe.log_error(
				title=f"AI Agent {agent_doc.name!r}: MCP connection {row.mcp_connection!r} not found, skipping"
			)
			continue
		if not doc.enabled:
			continue
		command_parts = shlex.split(doc.command or "")
		stored_args = json.loads(doc.command_args) if getattr(doc, "command_args", None) else None

		connection_dict = {
			"name": doc.name,
			"connection_type": doc.connection_type,
			"command": command_parts[0] if command_parts else doc.command,
			"command_args": stored_args if stored_args is not None else command_parts[1:],
			"endpoint_url": doc.endpoint_url,
			"environment_variables": json.loads(doc.environment_variables) if doc.environment_variables else {},
			"include_tools": json.loads(row.include_tools) if getattr(row, "include_tools", None) else None,
			"is_connected": bool(doc.is_connected),
			"status_message": doc.status_message,
		}

		if doc.connection_type == "streamable-http":
			connection_dict["api_key"] = doc.get("api_key")
			connection_dict["api_secret"] = doc.get_password("api_secret") if doc.get("api_secret") else None

		include_tools = connection_dict["include_tools"]
		if isinstance(include_tools, list):
			include_tools = [tool for tool in include_tools if tool not in direct_tool_names]
			if not include_tools:
				continue
			connection_dict["include_tools"] = include_tools
		resolved.append(connection_dict)
	return resolved


def _verify_service_secret() -> None:
	"""Verify the `X-Frappe-AI-Service-Secret` header against `site_config.json`.

	Raises:
		frappe.AuthenticationError: If the header is missing, the secret isn't
			configured, or it mismatches.
	"""
	provided = frappe.get_request_header("X-Frappe-AI-Service-Secret") or ""
	if not provided:
		frappe.throw(_("Missing service secret."), exc=frappe.AuthenticationError)

	expected = frappe.conf.get("frappe_ai_service_secret")

	if not expected or not _secrets_match(provided, expected):
		frappe.throw(_("Invalid service secret."), exc=frappe.AuthenticationError)


def _secrets_match(provided: str, expected: str) -> bool:
	"""Compare the supplied service secret with the configured secret in constant time."""
	return hmac.compare_digest(provided, expected)
