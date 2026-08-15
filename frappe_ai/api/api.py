# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Browser-facing whitelisted API — ported from `flow/api/api.py`.

**The load-bearing difference from `flow`:** none of these methods run the model.
`flow`'s `start_run`/`resume_run` call `FlowSession.chat()`/`.resume()` in-process and
return either a finished `Flow Run` summary or an SSE generator built from an
in-process event stream. Here, LLM orchestration lives in the FastAPI service (a
different process), so `start_run`/`resume_run` only:

1. Resolve/create the `AI Session` and persist this turn's messages (the parts that
   must happen in Frappe: authorization, transcript storage).
2. Create the `AI Run` row (status=Running) with a config snapshot.
3. Mint a short-lived run token (`frappe_ai.service.auth.mint_run_token`, ADR 0004).
4. Return `{run, session, token, stream_url}` — the browser opens the actual SSE
   connection **directly against FastAPI** (`POST {stream_url}` with
   `Authorization: Bearer <token>`), not through Frappe. See `001-architecture.md`
   §5.1 for the full sequence diagram this implements.

`stop_run`/`recover_session`/`submit_feedback`/`get_agent_tools`/`attach_file` port
much more directly, since they only ever touched DocTypes, never the runtime.

Two internal (service-secret-authenticated, not user-facing) callbacks live here too:
`persist_run_result` and `fail_run` — the Frappe-side half of the service's
"call back over HTTP on Done/exception/disconnect" persistence pattern that replaces
`flow`'s `stream_with_persistence` (dropped; see `AI Run`'s module docstring).

Thumbs-down feedback now also writes shared agent memory when the agent supports it.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

import frappe
from frappe import _

from frappe_ai.frappe_ai.doctype.ai_run.ai_run import assert_run_owner, create_run
from frappe_ai.frappe_ai.doctype.ai_session.ai_session import assert_session_owner, derive_title
from frappe_ai.frappe_ai.doctype.ai_session_attachment.ai_session_attachment import resolve_attachment
from frappe_ai.service.auth import DEFAULT_TTL_SECONDS, mint_run_token

FEEDBACK_COMMENT_LIMIT = 500


@frappe.whitelist()
def start_run(
	input: str,
	agent: str | None = None,
	session: str | None = None,
	model: str | None = None,
	attachments: list[str] | str | None = None,
) -> dict[str, Any]:
	"""Start a new turn: persist it in Frappe, then hand off to the FastAPI service.

	Creates a session if none is given (agent required in that case). Resuming an
	existing session reuses its locked-in agent; `model` may switch the session's
	model for this and future turns, unless a run is currently Paused or Running.

	Args:
		input (str): The user's message.
		agent (str | None): `AI Agent` name. Required when `session` is omitted.
		session (str | None): Existing `AI Session` name to continue.
		model (str | None): Override the agent's default model.
		attachments (list[str] | str | None): Uploaded `File` names (or a JSON array
			string of them) whose text is injected into this turn.

	Returns:
		dict[str, Any]: `{run, session, token, stream_url, expires_in}` — everything
			the browser needs to open the SSE stream directly against FastAPI.
	"""
	if not isinstance(input, str) or not input.strip():
		frappe.throw(_("Input is required."), title=_("Invalid Input"))

	files = _parse_attachments(attachments)
	session_doc = _resolve_session(session, agent=agent, model=model)
	session_doc.assert_not_blocked()

	agent_doc = frappe.get_doc("AI Agent", session_doc.agent)
	_check_agent_usable(agent_doc, session_doc.model)

	attachment_data = [resolve_attachment(f) for f in files]
	if not session_doc.title:
		session_doc.db_set("title", derive_title(input))

	run = create_run(
		source="Manual",
		input=input,
		session=session_doc.name,
		config_snapshot=agent_doc._snapshot(model=session_doc.model),
	)
	session_doc.persist_turn(input, agent_doc.instructions, attachment_data, run.name)

	return _mint_stream_response(run.name, session_doc.name, frappe.session.user)


@frappe.whitelist()
def resume_run(run_name: str, answers: dict[str, Any] | str) -> dict[str, Any]:
	"""Resume a Paused run with the user's answers to its pending questions.

	Args:
		run_name (str): The `AI Run` name to resume.
		answers (dict[str, Any] | str): Maps each question's `key` (the pausing tool
			call's id) to `"Approve"`, `"Deny"`, or free-text redirect feedback.

	Returns:
		dict[str, Any]: Same shape as `start_run` — a fresh token and stream URL.
	"""
	_parse_answers(answers)  # validated here so a malformed payload fails before minting a token

	run = frappe.get_doc("AI Run", run_name)
	assert_run_owner(run)
	if run.status != "Paused":
		frappe.throw(
			_("Only Paused runs can be resumed (this run is {0}).").format(run.status),
			title=_("Cannot Resume"),
		)

	return _mint_stream_response(run.name, run.session, frappe.session.user)


@frappe.whitelist()
def stop_run(run_name: str) -> dict[str, str]:
	"""Stop a run at the user's request: terminate a Paused run so the agent won't
	continue, or finalize a Running one whose SSE stream the client has aborted.

	Args:
		run_name (str): The `AI Run` name to stop.

	Returns:
		dict[str, str]: `{"status": <final AI Run status>}`.
	"""
	if not isinstance(run_name, str) or not run_name.strip():
		frappe.throw(_("Run is required."), title=_("Invalid Run"))

	run = frappe.get_doc("AI Run", run_name.strip())
	assert_run_owner(run)
	if run.status not in ("Completed", "Failed"):
		run.mark_failed("Stopped by user.")
	return {"status": run.status}


@frappe.whitelist()
def recover_session(session: str) -> dict[str, int]:
	"""Fail any Running run on session (re)load. The client that owned the stream is
	gone, so the run is abandoned; clearing it here unblocks the next turn instead of
	waiting for the stale-run timeout on the next send.

	Args:
		session (str): The `AI Session` name to recover.

	Returns:
		dict[str, int]: `{"recovered": <count of runs failed>}`.
	"""
	if not isinstance(session, str) or not session.strip():
		frappe.throw(_("Session is required."), title=_("Invalid Session"))

	doc = frappe.get_doc("AI Session", session.strip())
	assert_session_owner(doc)

	abandoned = frappe.get_all("AI Run", filters={"session": doc.name, "status": "Running"}, pluck="name")
	for name in abandoned:
		frappe.db.set_value(
			"AI Run",
			name,
			{"status": "Failed", "error": "Run abandoned: stream ended without completing."},
		)
	return {"recovered": len(abandoned)}


@frappe.whitelist()
def submit_feedback(run_name: str, rating: str, comment: str | None = None) -> dict[str, Any]:
	"""Record thumbs feedback on a finished run, or clear it with rating "None".

	Args:
		run_name (str): The `AI Run` name.
		rating (str): "Up", "Down", or "None" (clears any existing rating).
		comment (str | None): Optional comment, capped at 500 characters.

	Returns:
		dict[str, Any]: `{"rating": rating}` (or `{"rating": None}` when cleared).
	"""
	if not isinstance(run_name, str) or not run_name.strip():
		frappe.throw(_("Run is required."), title=_("Invalid Run"))
	if rating not in ("Up", "Down", "None"):
		frappe.throw(_("Rating must be Up, Down, or None."), title=_("Invalid Rating"))
	comment = (comment or "").strip()
	if len(comment) > FEEDBACK_COMMENT_LIMIT:
		frappe.throw(
			_("Keep feedback under {0} characters.").format(FEEDBACK_COMMENT_LIMIT),
			title=_("Feedback Too Long"),
		)

	run = frappe.get_doc("AI Run", run_name.strip())
	assert_run_owner(run)
	if run.status not in ("Completed", "Failed"):
		frappe.throw(
			_("Feedback applies to finished runs only (this run is {0}).").format(run.status),
			title=_("Run Not Finished"),
		)

	if rating == "None":
		run.db_set({"feedback_rating": "", "feedback_comment": None})
		return {"rating": None}

	run.db_set({"feedback_rating": rating, "feedback_comment": comment or None})
	if rating == "Down" and comment:
		from frappe_ai.memory.memory import save_feedback_memory

		save_feedback_memory(run, comment)
	return {"rating": rating}


@frappe.whitelist()
def get_agent_tools(agent: str) -> dict[str, bool]:
	"""Map an agent's tool slugs to whether each needs confirmation, so the panel can
	classify tool calls (approval vs. inline).

	Args:
		agent (str): `AI Agent` name.

	Returns:
		dict[str, bool]: `{slug: requires_confirmation}`.
	"""
	if not isinstance(agent, str) or not agent.strip():
		return {}

	doc = frappe.get_doc("AI Agent", agent.strip())
	frappe.has_permission("AI Agent", "read", doc.name, throw=True)

	tool_names = [row.tool for row in doc.tools]
	if not tool_names:
		return {}
	rows = frappe.get_all(
		"AI Tool", filters={"name": ["in", tool_names]}, fields=["slug", "requires_confirmation"]
	)
	return {row.slug: bool(row.requires_confirmation) for row in rows}


@frappe.whitelist()
def attach_file(file: str) -> dict[str, Any]:
	"""Validate and extract an uploaded File for use as a chat attachment. Errors
	(unsupported type, unreadable, not owned) surface here, at upload time. The
	extracted text is staged in cache; the attachment row is written on send.

	Args:
		file (str): `File` document name.

	Returns:
		dict[str, Any]: `{file, file_name, file_size}`.
	"""
	if not isinstance(file, str) or not file.strip():
		frappe.throw(_("File is required."), title=_("Invalid Attachment"))

	from frappe_ai.frappe_ai.doctype.ai_session_attachment.ai_session_attachment import stage_attachment

	return stage_attachment(file.strip())


@frappe.whitelist(allow_guest=True)
def persist_run_result(run: str, result: dict | str) -> dict[str, str]:
	"""Service-secret-authenticated callback: apply a finished/paused run segment.

	Called by the FastAPI service on `Done` (see `001-architecture.md` §8's `done`
	event) — the Frappe-side half of the persistence-via-explicit-callback pattern
	that replaces `flow`'s `stream_with_persistence`.

	Args:
		run (str): `AI Run` name.
		result (dict | str): `AIRun.apply_result`'s expected shape (JSON string or dict).

	Returns:
		dict[str, str]: `{"status": <new AI Run status>}`.

	Raises:
		frappe.AuthenticationError: If the service secret is missing or invalid.
	"""
	_verify_service_secret()
	if isinstance(result, str):
		result = json.loads(result)
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		doc = frappe.get_doc("AI Run", run)
		doc.apply_result(result)
		return {"status": doc.status}
	finally:
		frappe.set_user(original_user)


@frappe.whitelist(allow_guest=True)
def fail_run(run: str, error: str) -> dict[str, str]:
	"""Service-secret-authenticated callback: mark a run Failed.

	Called by the FastAPI service when a run raises or the client disconnects
	mid-stream before a `Done` event was ever produced (`001-architecture.md` §9).

	Args:
		run (str): `AI Run` name.
		error (str): Error message (truncated to 5000 chars by `AIRun.mark_failed`).

	Returns:
		dict[str, str]: `{"status": "Failed"}`.

	Raises:
		frappe.AuthenticationError: If the service secret is missing or invalid.
	"""
	_verify_service_secret()
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		doc = frappe.get_doc("AI Run", run)
		doc.mark_failed(error)
		return {"status": doc.status}
	finally:
		frappe.set_user(original_user)


def _verify_service_secret() -> None:
	"""Same check as `api/service.py`/`api/dispatch.py` — see either's docstring."""
	provided = frappe.get_request_header("X-Frappe-AI-Service-Secret") or ""
	if not provided:
		frappe.throw(_("Missing service secret."), exc=frappe.AuthenticationError)

	expected = frappe.conf.get("frappe_ai_service_secret")
	if not expected or not hmac.compare_digest(provided, expected):
		frappe.throw(_("Invalid service secret."), exc=frappe.AuthenticationError)


def _resolve_session(session: str | None, *, agent: str | None, model: str | None):
	"""Load an existing session (owner-checked) or create a new one bound to `agent`."""
	if session:
		doc = frappe.get_doc("AI Session", session)
		assert_session_owner(doc)
		if model and model != doc.model:
			doc.assert_not_blocked()  # refuse to switch mid-flight; also protects resume
			doc.model = model
			doc.save(ignore_permissions=True)
		return doc

	if not agent:
		frappe.throw(_("Agent is required to start a new session."), title=_("Missing Agent"))

	agent_doc = frappe.get_doc("AI Agent", agent)
	frappe.has_permission("AI Agent", "read", agent_doc.name, throw=True)

	return frappe.get_doc(
		{
			"doctype": "AI Session",
			"agent": agent_doc.name,
			"model": model or None,
			"source": "Manual",
		}
	).insert(ignore_permissions=True)


def _check_agent_usable(agent_doc, session_model: str | None) -> None:
	"""Mirrors `AI Agent.assemble()`'s permission gate (flow) — but flow's `assemble()`
	built a runtime agent object; here it only needs to fail fast before a token is
	minted, so the service never has to reject a config it was never allowed to see."""
	if not agent_doc.enabled:
		frappe.throw(_("AI Agent {0} is disabled.").format(agent_doc.name), title=_("Disabled Agent"))

	model_name = session_model or agent_doc.model
	if not frappe.has_permission("AI Model", "read", model_name):
		frappe.throw(
			_("You are not permitted to use AI Model {0}.").format(model_name),
			frappe.PermissionError,
			title=_("Model Not Permitted"),
		)
	if not frappe.db.get_value("AI Model", model_name, "enabled"):
		frappe.throw(_("AI Model {0} is disabled.").format(model_name), title=_("Disabled Model"))


def _mint_stream_response(run: str, session: str, user: str) -> dict[str, Any]:
	"""Mint a run token and build the payload the browser needs to open the SSE stream
	directly against FastAPI (`001-architecture.md` §5.1, steps after `start_run`)."""
	secret = frappe.conf.get("frappe_ai_service_secret")
	if not secret:
		frappe.throw(
			_("frappe_ai_service_secret is not set in site_config.json."), title=_("Service Not Configured")
		)
	service_base_url = frappe.get_cached_value("AI Settings", "AI Settings", "service_base_url")
	token = mint_run_token(run=run, session=session, user=user, secret=secret)

	return {
		"run": run,
		"session": session,
		"token": token,
		"stream_url": f"{(service_base_url or '').rstrip('/')}/stream/{run}",
		"expires_in": DEFAULT_TTL_SECONDS,
	}


def _parse_attachments(value: Any) -> list[str]:
	"""Normalize the attachments argument (a list, a JSON-array string, or empty) to file names."""
	if not value:
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			frappe.throw(_("Attachments must be a JSON array of file ids."), title=_("Invalid Attachments"))
	if not isinstance(value, list):
		frappe.throw(_("Attachments must be a list of file ids."), title=_("Invalid Attachments"))
	files: list[str] = []
	for file in value:
		if not isinstance(file, str) or not file.strip():
			frappe.throw(_("Each attachment must be a file id."), title=_("Invalid Attachments"))
		files.append(file.strip())
	return files


def _parse_answers(answers: Any) -> dict[str, Any]:
	if isinstance(answers, str):
		try:
			answers = json.loads(answers)
		except (TypeError, ValueError):
			frappe.throw(_("Answers must be a JSON object."), title=_("Invalid Answers"))
	if not isinstance(answers, dict):
		frappe.throw(_("Answers must be a JSON object."), title=_("Invalid Answers"))
	return answers
