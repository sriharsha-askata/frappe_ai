# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Browser-facing run, session, feedback, and service callback endpoints."""

from __future__ import annotations

import hmac
import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime

from frappe_ai.api._service_url import get_service_url
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import (
	RunAlreadyFinished,
	assert_run_owner,
	create_run,
	record_approvals,
)
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
	"""Persist a new turn and return its run, session, token, and stream URL.

	A new session requires an agent. An existing session keeps its agent; a model
	override applies to this and later turns unless another run is active."""
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
	"""Resume a paused run with answers keyed by pending tool-call ID.

	Accept Approve, Deny, or redirect feedback; return a new token and stream URL."""
	parsed_answers = _parse_answers(answers)  # validated here so a malformed payload fails before minting a token

	run = frappe.get_doc("AI Run", run_name)
	assert_run_owner(run)
	if run.status != "Paused":
		frappe.throw(
			_("Only Paused runs can be resumed (this run is {0}).").format(run.status),
			title=_("Cannot Resume"),
		)

	# The approval is recorded here, by the logged-in user, so dispatch can enforce it in
	# Frappe instead of trusting the service to have asked.
	record_approvals(run, parsed_answers)
	# The runtime budget measures active time: a resume starts a fresh segment.
	run.db_set("segment_started_at", now_datetime(), update_modified=False)

	return _mint_stream_response(run.name, run.session, frappe.session.user)


@frappe.whitelist()
def stop_run(run_name: str) -> dict[str, str]:
	"""Stop a Paused or Running run and return its final status."""
	if not isinstance(run_name, str) or not run_name.strip():
		frappe.throw(_("Run is required."), title=_("Invalid Run"))

	# Lock the row so the status check and the write cannot interleave with the service
	# finishing the run; a run that is already finished is simply reported as it is.
	run = frappe.get_doc("AI Run", run_name.strip(), for_update=True)
	assert_run_owner(run)
	try:
		run.mark_failed("Stopped by user.")
	except RunAlreadyFinished:
		pass
	return {"status": run.status}


@frappe.whitelist()
def recover_session(session: str) -> dict[str, int]:
	"""Fail abandoned Running runs when a session is reloaded."""
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
	"""Set or clear thumbs feedback for a finished run; comments cap at 500 chars."""
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
	"""Return `{slug: requires_confirmation}` for an agent's tools."""
	if not isinstance(agent, str) or not agent.strip():
		return {}

	doc = frappe.get_doc("AI Agent", agent.strip())
	frappe.has_permission("AI Agent", "read", doc.name, throw=True)

	tool_names = [row.tool_name for row in doc.tools]
	if not tool_names:
		return {}
	rows = frappe.get_all(
		"AI Tool", filters={"name": ["in", tool_names]}, fields=["slug", "requires_confirmation"]
	)
	return {row.slug: bool(row.requires_confirmation) for row in rows}


@frappe.whitelist()
def attach_file(file: str) -> dict[str, Any]:
	"""Validate and stage an uploaded file; return metadata for the composer."""
	if not isinstance(file, str) or not file.strip():
		frappe.throw(_("File is required."), title=_("Invalid Attachment"))

	from frappe_ai.frappe_ai.doctype.ai_session_attachment.ai_session_attachment import stage_attachment

	return stage_attachment(file.strip())


@frappe.whitelist(allow_guest=True)
def persist_run_result(run: str, result: dict | str) -> dict[str, str]:
	"""Apply a completed or paused service segment to its run and session.

	Accept `result` as a dict or JSON string; return the resulting run status."""
	_verify_service_secret()
	if isinstance(result, str):
		result = json.loads(result)
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		doc = frappe.get_doc("AI Run", run, for_update=True)
		try:
			doc.apply_result(result)
		except RunAlreadyFinished:
			return {"status": doc.status, "ignored": True}
		return {"status": doc.status}
	finally:
		frappe.set_user(original_user)


@frappe.whitelist(allow_guest=True)
def fail_run(run: str, error: str) -> dict[str, str]:
	"""Mark a run Failed after a service error or stream disconnect."""
	_verify_service_secret()
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		doc = frappe.get_doc("AI Run", run, for_update=True)
		try:
			doc.mark_failed(error)
		except RunAlreadyFinished:
			return {"status": doc.status, "ignored": True}
		return {"status": doc.status}
	finally:
		frappe.set_user(original_user)


@frappe.whitelist(allow_guest=True)
def log_diagnostic(title: str, message: str) -> dict[str, str]:
	"""Record a service-side diagnostic in Frappe's Error Log."""
	_verify_service_secret()
	frappe.log_error(title=title, message=message)
	return {"status": "logged"}


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
	"""Reject an unusable agent before minting a run token.

	This keeps the service from receiving a config the caller cannot access.
	"""
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
	service_url = get_service_url()
	token = mint_run_token(run=run, session=session, user=user, secret=secret)

	return {
		"run": run,
		"session": session,
		"token": token,
		"stream_url": f"{service_url}/stream/{run}",
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
