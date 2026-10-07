# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Frontend-oriented JSON API for same-origin custom UIs."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils.file_manager import save_file

from frappe_ai.api import api
from frappe_ai.api.frontend_view import (
	_attachment_row,
	_display_title,
	_format_value,
	_input_summary,
	_mcp_connection_row,
	_parse_json,
	_result_summary,
	_session_row,
	_summarize_text,
	_tool_count,
	_tool_identity,
	_transcript,
)
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import assert_run_owner
from frappe_ai.frappe_ai.doctype.ai_session.ai_session import assert_session_owner
from frappe_ai.knowledge.extract import FILE_EXTENSIONS


def _tool_summaries(agent_doc) -> list[dict[str, Any]]:
	items: list[dict[str, Any]] = []
	seen: set[str] = set()

	plugin_rows = [
		row
		for row in getattr(agent_doc, "plugin_tools", []) or []
		if getattr(row, "enabled", False) and getattr(row, "fac_tool", None)
	]
	if plugin_rows:
		try:
			from frappe_assistant_core.core.tool_registry import get_tool_registry

			available = {
				item.get("name"): item
				for item in get_tool_registry().get_available_tools(user=frappe.session.user)
				if item.get("name")
			}
		except Exception:
			available = {}

		for row in plugin_rows:
			tool_name = row.fac_tool
			metadata = available.get(tool_name)
			if not metadata or tool_name in seen:
				continue
			description = metadata.get("description") or ""
			items.append(
				{
					"id": tool_name,
					"name": tool_name,
					"display_name": tool_name.replace("_", " ").title(),
					"description": description,
					"requires_confirmation": bool(row.requires_confirmation),
					"input_schema": metadata.get("inputSchema") or {},
					"summary": _summarize_text(description, limit=120),
				}
			)
			seen.add(tool_name)

	return items


def _agent_row(row: Any) -> dict[str, Any]:
	agent_doc = frappe.get_doc("AI Agent", row.name)
	model_name = agent_doc.model
	model_title = frappe.db.get_value("AI Model", model_name, "title") if model_name else None
	mcp_connections: list[dict[str, Any]] = []
	for link in getattr(agent_doc, "mcp_connections", []) or []:
		try:
			doc = frappe.get_doc("AI MCP Connection", link.mcp_connection)
		except frappe.DoesNotExistError:
			continue
		if not doc.enabled:
			continue
		mcp_connections.append(_mcp_connection_row(doc))
	tool_summaries = _tool_summaries(agent_doc)
	return {
		"id": row.name,
		"name": row.name,
		"title": _display_title(row.name, row.title),
		"readiness": {
			"state": "ready" if model_name else "needs_model",
			"label": _("Ready") if model_name else _("Model required"),
		},
		"model": {
			"name": model_name,
			"title": _display_title(model_name, model_title),
		},
		"tools": {
			"count": len(tool_summaries),
			"summaries": tool_summaries,
		},
		"mcp_connections": mcp_connections,
		"prompt_summary": _summarize_text(agent_doc.instructions, limit=180),
		"output_summary": _("Markdown enabled") if agent_doc.markdown else _("Plain output"),
		"configure_action": {"label": _("Configure agent"), "target": f"/app/ai-agent/{agent_doc.name}"},
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


def _active_run(session: str) -> dict[str, Any] | None:
	rows = frappe.get_all(
		"AI Run",
		filters={"session": session, "status": ["in", ["Running", "Paused"]]},
		fields=["name", "status", "creation", "modified", "error"],
		order_by="creation desc",
		limit=1,
	)
	if not rows:
		return None
	row = rows[0]
	return {
		"run": row.name,
		"status": row.status,
		"started_at": row.creation,
		"updated_at": row.modified,
		"error": row.error or "",
	}


def _bootstrap_payload(*, current_session: dict[str, Any] | None = None) -> dict[str, Any]:
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
	history = frappe.get_all(
		"AI Session",
		filters={"owner": frappe.session.user, "source": ["!=", "Trigger"]},
		fields=["name", "title", "modified", "agent", "model", "source"],
		order_by="modified desc",
		limit=50,
	)
	agent_rows = [_agent_row(row) for row in agents]
	selected_agent = current_session.get("agent") if current_session else None
	if not selected_agent and agent_rows:
		selected_agent = next((row["name"] for row in agent_rows if row["name"] == "Frappe AI"), agent_rows[0]["name"])
	return {
		"user": {
			"name": frappe.session.user,
			"full_name": frappe.utils.get_fullname(frappe.session.user),
		},
		"agent": {
			"selected": selected_agent,
			"items": agent_rows,
			"models": [
				{
					"name": row.name,
					"title": _display_title(row.name, row.title),
				}
				for row in models
			],
		},
		"session": {
			"current": current_session,
			"history": [_session_row(row) for row in history],
		},
		"execution": {
			"current_run": None,
			"transcript": [],
			"paused_run": None,
			"feedback": [],
		},
		"composer": {
			"supported_file_types": sorted(FILE_EXTENSIONS),
		},
		"capabilities": {
			"standalone_page": True,
			"panel": True,
			"custom_frontend": True,
			"stream_transport": "fastapi_bearer_sse",
		},
	}


@frappe.whitelist()
def bootstrap() -> dict[str, Any]:
	return _bootstrap_payload()


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
	return {"session": {"history": [_session_row(row) for row in rows]}}


@frappe.whitelist()
def session_detail(session: str) -> dict[str, Any]:
	if not isinstance(session, str) or not session.strip():
		frappe.throw(_("Session is required."), title=_("Invalid Session"))

	doc = frappe.get_doc("AI Session", session.strip())
	assert_session_owner(doc)
	feedback = _feedback_rows(doc.name)
	paused_run = _paused_run(doc.name)
	session_row = {
		"id": doc.name,
		"name": doc.name,
		"title": _display_title(doc.name, doc.title),
		"preview": _summarize_text(doc.title or doc.name, limit=72),
		"agent": doc.agent,
		"model": doc.model,
		"source": doc.source,
		"modified": doc.modified,
	}
	return {
		"agent": {
			"selected": doc.agent,
		},
		"session": {
			"current": session_row,
		},
		"execution": {
			"current_run": _active_run(doc.name),
			"transcript": _transcript(doc, feedback, paused_run),
			"paused_run": paused_run,
			"feedback": feedback,
			"attachments": [_attachment_row(row) for row in doc.attachments],
		},
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
	if not isinstance(agent, str) or not agent.strip():
		return {"tools": {"count": 0, "summaries": []}, "mcp_connections": []}
	doc = frappe.get_doc("AI Agent", agent.strip())
	frappe.has_permission("AI Agent", "read", doc.name, throw=True)
	tools = _tool_summaries(doc)
	mcps: list[dict[str, Any]] = []
	for row in getattr(doc, "mcp_connections", []) or []:
		try:
			connection_doc = frappe.get_doc("AI MCP Connection", row.mcp_connection)
		except frappe.DoesNotExistError:
			continue
		if not connection_doc.enabled:
			continue
		mcps.append(_mcp_connection_row(connection_doc))
	return {
		"tools": {"count": len(tools), "summaries": tools},
		"mcp_connections": mcps,
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
