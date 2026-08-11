# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Frontend-oriented JSON API for same-origin custom UIs.

These methods keep the existing Frappe + FastAPI execution contract, but expose
normalized payloads so React shells do not need Desk internals, `frappe.client`,
or raw DocType shapes.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils.file_manager import save_file

from frappe_ai.api import api
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import assert_run_owner
from frappe_ai.frappe_ai.doctype.ai_session.ai_session import assert_session_owner
from frappe_ai.knowledge.extract import FILE_EXTENSIONS


def _parse_json(value: str | None, fallback: Any) -> Any:
	if not value:
		return fallback
	try:
		return json.loads(value)
	except Exception:
		return fallback


def _session_row(row: Any) -> dict[str, Any]:
	return {
		"name": row.name,
		"title": row.title,
		"modified": row.modified,
		"agent": getattr(row, "agent", None),
		"model": getattr(row, "model", None),
		"source": getattr(row, "source", None),
	}


def _message_row(row: Any) -> dict[str, Any]:
	return {
		"name": row.name,
		"role": row.role,
		"content": row.content,
		"run": row.run,
		"tool_call_id": row.tool_call_id,
		"tool_calls": _parse_json(row.tool_calls, []),
	}


def _attachment_row(row: Any) -> dict[str, Any]:
	return {
		"name": row.name,
		"run": row.run,
		"file": row.file,
		"file_name": row.file_name,
		"file_size": row.file_size,
		"mode": row.mode,
	}


def _feedback_rows(session: str) -> list[dict[str, Any]]:
	rows = frappe.get_all(
		"AI Run",
		filters={"session": session, "feedback_rating": ["is", "set"]},
		fields=["name", "feedback_rating", "feedback_comment"],
		order_by="creation asc",
		limit=100,
	)
	return [
		{
			"run": row.name,
			"rating": row.feedback_rating,
			"comment": row.feedback_comment or "",
		}
		for row in rows
	]


def _paused_run(session: str) -> dict[str, Any] | None:
	rows = frappe.get_all(
		"AI Run",
		filters={"session": session, "status": "Paused"},
		fields=["name", "questions"],
		order_by="creation desc",
		limit=1,
	)
	if not rows:
		return None
	return {
		"run": rows[0].name,
		"questions": _parse_json(rows[0].questions, []),
	}


@frappe.whitelist()
def bootstrap() -> dict[str, Any]:
	agents = frappe.get_all(
		"AI Agent",
		filters={"enabled": 1},
		fields=["name", "title"],
		order_by="modified desc",
		limit=50,
	)
	models = frappe.get_all(
		"AI Model",
		filters={"enabled": 1},
		fields=["name", "title"],
		order_by="modified desc",
		limit=50,
	)
	recent_sessions = frappe.get_all(
		"AI Session",
		filters={"owner": frappe.session.user, "source": ["!=", "Trigger"]},
		fields=["name", "title", "modified", "agent", "model", "source"],
		order_by="modified desc",
		limit=15,
	)
	return {
		"user": {
			"name": frappe.session.user,
			"full_name": frappe.utils.get_fullname(frappe.session.user),
		},
		"agents": agents,
		"models": models,
		"recent_sessions": [_session_row(row) for row in recent_sessions],
		"supported_file_types": sorted(FILE_EXTENSIONS),
		"capabilities": {
			"standalone_page": True,
			"panel": True,
			"custom_frontend": True,
			"stream_transport": "fastapi_bearer_sse",
		},
	}


@frappe.whitelist()
def sessions(query: str | None = None, limit: int = 20) -> dict[str, Any]:
	filters: dict[str, Any] = {"owner": frappe.session.user, "source": ["!=", "Trigger"]}
	if isinstance(query, str) and query.strip():
		escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
		filters["title"] = ["like", f"%{escaped}%"]
	rows = frappe.get_all(
		"AI Session",
		filters=filters,
		fields=["name", "title", "modified", "agent", "model", "source"],
		order_by="modified desc",
		limit=max(1, min(int(limit or 20), 100)),
	)
	return {"sessions": [_session_row(row) for row in rows]}


@frappe.whitelist()
def session_detail(session: str) -> dict[str, Any]:
	if not isinstance(session, str) or not session.strip():
		frappe.throw(_("Session is required."), title=_("Invalid Session"))

	doc = frappe.get_doc("AI Session", session.strip())
	assert_session_owner(doc)
	return {
		"session": {
			"name": doc.name,
			"title": doc.title,
			"agent": doc.agent,
			"model": doc.model,
			"source": doc.source,
			"modified": doc.modified,
		},
		"messages": [_message_row(row) for row in doc.messages],
		"attachments": [_attachment_row(row) for row in doc.attachments],
		"paused_run": _paused_run(doc.name),
		"feedback": _feedback_rows(doc.name),
	}


@frappe.whitelist()
def start_run(
	input: str,
	agent: str | None = None,
	session: str | None = None,
	model: str | None = None,
	attachments: list[str] | str | None = None,
) -> dict[str, Any]:
	return api.start_run(input=input, agent=agent, session=session, model=model, attachments=attachments)


@frappe.whitelist()
def resume_run(run: str, answers: dict[str, Any] | str) -> dict[str, Any]:
	return api.resume_run(run_name=run, answers=answers)


@frappe.whitelist()
def stop_run(run: str) -> dict[str, Any]:
	return api.stop_run(run_name=run)


@frappe.whitelist()
def recover_session(session: str) -> dict[str, Any]:
	return api.recover_session(session=session)


@frappe.whitelist()
def submit_feedback(run: str, rating: str, comment: str | None = None) -> dict[str, Any]:
	return api.submit_feedback(run_name=run, rating=rating, comment=comment)


@frappe.whitelist()
def upload_attachment() -> dict[str, Any]:
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	if "file" not in frappe.request.files:
		frappe.throw(_("File is required."), title=_("Invalid Attachment"))

	uploaded = frappe.request.files["file"]
	file_doc = save_file(
		uploaded.filename or "attachment",
		uploaded.stream.read(),
		dt=None,
		dn=None,
		is_private=1,
	)
	attachment = api.attach_file(file_doc.name)
	return {"attachment": attachment}


@frappe.whitelist()
def agent_tools(agent: str) -> dict[str, Any]:
	tool_map = api.get_agent_tools(agent)
	return {
		"tools": {
			slug: {"requires_confirmation": requires_confirmation}
			for slug, requires_confirmation in tool_map.items()
		}
	}


@frappe.whitelist()
def run_feedback(run: str) -> dict[str, Any]:
	if not isinstance(run, str) or not run.strip():
		frappe.throw(_("Run is required."), title=_("Invalid Run"))
	doc = frappe.get_doc("AI Run", run.strip())
	assert_run_owner(doc)
	return {
		"run": doc.name,
		"rating": doc.feedback_rating or None,
		"comment": doc.feedback_comment or "",
	}
