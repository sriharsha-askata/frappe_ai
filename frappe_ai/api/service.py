# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Frappe-side endpoints supporting the FastAPI service.

Whitelisted methods:
- `mint_token` — mints an HMAC run token (ADR 0004). Now validates against real
  `AI Run`/`AI Session` documents (Phase 3) — see its docstring.
- `service_health` — lets Frappe check whether the FastAPI service is reachable,
  by calling its `GET /health`.
- `get_service_config` — the Frappe-side half of `frappe_client.py`'s Phase 2
  capability. Authenticates the FastAPI service via the shared secret carried
  as a dedicated `X-Frappe-AI-Service-Secret` header (not `Authorization` — see
  `_verify_service_secret`'s docstring for why), not a logged-in user session.
- `get_run_config` (Phase 3) — the config fetch in `001-architecture.md` §5.1's
  step 3: given a run token's payload, returns everything `AgentBuilder.build()`
  needs — agent fields, resolved model/provider credentials, each bound tool's
  JSON Schema, and the prompt messages for this turn. Same shared-secret auth
  as `get_service_config`, plus the acting user is passed explicitly (not
  inferred from a Frappe session, since the service has none) so permission
  checks below run as that user.

Shared secret: `frappe_ai_service_secret` in `site_config.json` (ADR 0010,
revised) — not a DocType field. This process reads it via `frappe.conf`, the
same file every Frappe process already loads; the FastAPI service reads the
same file directly off disk (`frappe_ai/service/config.py`), since it never
calls `frappe.init`/`frappe.connect`. One file, one source of truth, no
DB-stored secret to keep in sync with anything.
"""

from __future__ import annotations

import hmac
import json
import shlex

import requests

import frappe
from frappe import _
from frappe.utils import cint

from frappe_ai.lib.model import get_model_class
from frappe_ai.service.auth import DEFAULT_TTL_SECONDS, mint_run_token

SERVICE_HEALTH_TIMEOUT = 5


@frappe.whitelist()
def mint_token(run: str, session: str, ttl_seconds: int | None = None) -> dict:
	"""Mint a short-lived, single-run HMAC token for the calling user.

	`AI Run`/`AI Session` exist as of Phase 3: this verifies the run belongs to
	the given session, the run is still `Running`, and the caller owns the run
	(`assert_run_owner` — owner match or `write` permission) before minting.
	`frappe_ai.api.api.start_run`/`resume_run` are the primary callers in practice
	(they mint inline via `frappe_ai.service.auth.mint_run_token` directly, since
	they already hold the freshly created/loaded run doc); this whitelisted
	wrapper exists for any client that only has a run name.

	Args:
		run (str): `AI Run` name the token will be bound to.
		session (str): `AI Session` name the run belongs to.
		ttl_seconds (int | None): Token lifetime in seconds. Defaults to
			`auth.DEFAULT_TTL_SECONDS` (300s) when omitted.

	Returns:
		dict: `{"success": True, "data": {"token": str, "expires_in": int}}`
	"""
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
	settings = frappe.get_cached_doc("AI Settings")
	base_url = settings.service_base_url
	if not base_url:
		return {
			"success": False,
			"message": _("AI Settings.service_base_url is not configured."),
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
	"""Return the `AI Settings` subset the FastAPI service needs, over HTTP.

	Authenticated with the shared secret carried as a custom
	`X-Frappe-AI-Service-Secret` header (not the standard `Authorization` header —
	see `frappe_client.py`'s module docstring for why: Frappe core's own
	`validate_auth()` intercepts `Authorization: Bearer ...` before this method's
	body would run), verified against `frappe.conf.frappe_ai_service_secret`
	(`site_config.json`). Not a logged-in user session, since the FastAPI service
	has none. `allow_guest=True` is required for this header-only auth to run at
	all; the 401 below is what actually gates access.

	Returns:
		dict: The service-relevant subset of `AI Settings`.

	Raises:
		frappe.AuthenticationError: If the bearer secret is missing or does not match.
	"""
	_verify_service_secret()

	settings = frappe.get_cached_doc("AI Settings")
	return {
		"request_timeout": settings.request_timeout,
		"stream_timeout": settings.stream_timeout,
		"lancedb_path": settings.lancedb_path,
	}


@frappe.whitelist(allow_guest=True)
def get_run_config(run: str, user: str) -> dict:
	"""Return everything `AgentBuilder.build()` needs to run one turn: agent fields,
	resolved model call kwargs, each bound tool's JSON Schema, and this session's
	prompt messages.

	Same shared-secret auth as `get_service_config`. `user` is the acting user
	carried explicitly (the service has no Frappe session of its own) — every
	permission check below (`AI Model` read, tool resolution) runs as this user via
	`frappe.set_user`, mirroring `AI Agent.assemble()`'s permission gate in `flow`.

	Args:
		run (str): `AI Run` name — already verified `Running` and owned by `user`
			at `mint_token`/`start_run` time; re-checked here since a run token's
			TTL can outlive a fast-moving status change.
		user (str): The Frappe user this run belongs to.

	Returns:
		dict: `{
			"agent": {"name", "instructions", "max_iterations", "temperature",
				"top_p", "reasoning", "markdown"},
			"model": {"class_module", "class_name", "model_id", "api_key",
				"base_url", "params"},
			"tools": [{"name", "description", "parameters", "requires_confirmation"}, ...],
			"mcp_connections": [...],
			"messages": [...],  # this session's full prompt, from AISession.build_prompt_messages()
			"config_snapshot": {...},  # AI Run.config_snapshot, already stored at start_run time
			"questions": [{"key", "name", "arguments", "prompt"}, ...],  # pending confirmations
				# from the prior segment, empty on a fresh run — resume dispatches
				# approved ones directly using these arguments (see chat.py's
				# _dispatch_approved, not by hoping the model re-requests the call)
		}`

	Raises:
		frappe.AuthenticationError: If the bearer secret is missing or does not match.
	"""
	_verify_service_secret()
	original_user = frappe.session.user
	frappe.set_user(user)
	try:
		run_doc = frappe.get_doc("AI Run", run)
		if run_doc.status != "Running" and run_doc.status != "Paused":
			frappe.throw(_("Run {0} is not active (status: {1}).").format(run, run_doc.status))

		session_doc = frappe.get_doc("AI Session", run_doc.session)
		if session_doc.owner != user:
			frappe.throw(_("Run {0} does not belong to {1}.").format(run, user), frappe.PermissionError)

		agent_doc = frappe.get_doc("AI Agent", session_doc.agent)
		model_name = session_doc.model or agent_doc.model
		if not frappe.has_permission("AI Model", "read", model_name):
			frappe.throw(
				_("{0} is not permitted to use AI Model {1}.").format(user, model_name), frappe.PermissionError
			)
		model_doc = frappe.get_doc("AI Model", model_name)
		if not model_doc.enabled:
			frappe.throw(_("AI Model {0} is disabled.").format(model_name), title=_("Disabled Model"))

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
			"tools": _resolve_agent_plugin_tools(agent_doc, user),
			"mcp_connections": _resolve_agent_mcp_connections(agent_doc),
			"messages": session_doc.build_prompt_messages(),
			"config_snapshot": json.loads(run_doc.config_snapshot) if run_doc.config_snapshot else {},
			# Pending confirmations from the prior segment, `[{key, name, arguments,
			# prompt}, ...]` — the arguments a resume's approved calls must actually be
			# dispatched with. Empty on a fresh (non-resumed) run.
			"questions": json.loads(run_doc.questions) if run_doc.questions else [],
		}
	finally:
		frappe.set_user(original_user)


def _model_call_config(model_doc) -> dict:
	"""Resolve an `AI Model` doc into what `AgentBuilder` needs to instantiate the
	Agno model class — a hard two-state split on whether `provider` (`Link → AI
	Provider`) is set, per ADR 0013.

	Linked: the Agno class and all credentials/extra params come from the linked `AI
	Provider` doc alone — this model's own `api_key`/`base_url` fields are not read.

	Unlinked: no provider slug to resolve an Agno class from, so this model's own
	`api_key`/`base_url` are used directly against `agno.models.openai.OpenAIChat` —
	the same "any OpenAI-wire-compatible endpoint via `base_url`" pattern proven live
	against Groq in Phase 3, now the automatic behaviour for an unlinked model instead
	of requiring `provider="openai"` to be typed in.
	"""
	if model_doc.provider:
		provider_doc = frappe.get_doc("AI Provider", model_doc.provider)
		model_class = get_model_class(provider_doc.provider)
		api_key = provider_doc.get_password("api_key", raise_exception=False)
		base_url = provider_doc.base_url
		extra_params = json.loads(provider_doc.extra_params) if provider_doc.extra_params else {}
	else:
		from agno.models.openai import OpenAIChat

		model_class = OpenAIChat
		api_key = model_doc.get_password("api_key", raise_exception=False)
		base_url = model_doc.base_url
		extra_params = {}

	return {
		"provider": model_doc.provider,
		"class_module": model_class.__module__,
		"class_name": model_class.__name__,
		"model_id": model_doc.model_id,
		"api_key": api_key,
		"base_url": base_url,
		"params": {
			**extra_params,
			**(json.loads(model_doc.params) if model_doc.params else {}),
		},
	}


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


def _resolve_agent_mcp_connections(agent_doc) -> list[dict]:
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
	"""Constant-time comparison of the provided secret against the configured one.

	Args:
		provided (str): Secret from the `X-Frappe-AI-Service-Secret` header.
		expected (str): `frappe.conf.frappe_ai_service_secret` (`site_config.json`).

	Returns:
		bool: True if they match.
	"""
	return hmac.compare_digest(provided, expected)
