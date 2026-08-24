# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Frontend-oriented JSON API for same-origin custom UIs."""

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


def _display_title(name: str | None, title: str | None = None) -> str:
	return (title or name or "").strip()


def _summarize_text(value: str | None, *, limit: int = 160) -> str:
	text = " ".join((value or "").strip().split())
	if len(text) <= limit:
		return text
	return f"{text[: limit - 1].rstrip()}…"


def _format_value(value: Any) -> str:
	if value is None:
		return ""
	if isinstance(value, str):
		return _summarize_text(value, limit=80)
	if isinstance(value, (int, float, bool)):
		return str(value)
	if isinstance(value, list):
		return ", ".join(_format_value(item) for item in value[:3])
	if isinstance(value, dict):
		return ", ".join(f"{key}: {_format_value(item)}" for key, item in list(value.items())[:3])
	return _summarize_text(str(value), limit=80)


def _input_summary(arguments: Any) -> list[dict[str, str]]:
	if not isinstance(arguments, dict):
		return []
	rows: list[dict[str, str]] = []
	for key, value in list(arguments.items())[:4]:
		formatted = _format_value(value)
		if formatted:
			rows.append({"label": str(key), "value": formatted})
	return rows


def _result_summary(result: str | None) -> str:
	if not result:
		return ""
	parsed = _parse_json(result, None)
	if isinstance(parsed, dict):
		for key in ("error", "message", "status", "result"):
			value = parsed.get(key)
			if value:
				return _format_value(value)
		return _format_value(parsed)
	if isinstance(parsed, list):
		return _format_value(parsed)
	return _summarize_text(result, limit=140)


def _tool_identity(raw_name: str | None) -> dict[str, Any]:
	name = (raw_name or "").strip()
	if "<|" not in name:
		return {"kind": "tool", "tool_name": name, "connection_name": None}
	tool_name, connection_name = name.split("<|", 1)
	return {
		"kind": "mcp_tool",
		"tool_name": tool_name.strip(),
		"connection_name": connection_name.strip() or None,
	}


def _tool_count(status_message: str | None) -> int | None:
	if not isinstance(status_message, str):
		return None
	prefix = "Connected ("
	suffix = " tools)"
	if status_message.startswith(prefix) and status_message.endswith(suffix):
		value = status_message[len(prefix) : -len(suffix)]
		if value.isdigit():
			return int(value)
	return None


def _tool_summary_from_doc(tool_name: str) -> dict[str, Any] | None:
	try:
		tool_doc = frappe.get_doc("AI Tool", tool_name)
	except frappe.DoesNotExistError:
		return None
	if not tool_doc.enabled:
		return None
	runtime_tool = tool_doc.to_tool()
	return {
		"id": runtime_tool.name,
		"name": runtime_tool.name,
		"display_name": tool_doc.title or runtime_tool.name,
		"description": runtime_tool.description,
		"requires_confirmation": bool(runtime_tool.requires_confirmation),
		"input_schema": runtime_tool.parameters,
		"summary": tool_doc.summary or _summarize_text(runtime_tool.description, limit=120),
	}


def _tool_summary_from_row(row) -> dict[str, Any] | None:
	tool_name = getattr(row, "tool", None)
	if not tool_name:
		return None
	return _tool_summary_from_doc(tool_name)


def _tool_summaries(agent_doc) -> list[dict[str, Any]]:
	items: list[dict[str, Any]] = []
	for row in getattr(agent_doc, "tools", []) or []:
		summary = _tool_summary_from_row(row)
		if summary:
			items.append(summary)
	return items


def _mcp_connection_row(doc) -> dict[str, Any]:
	status = "connected" if doc.is_connected else "disconnected"
	tool_summaries = [
		{
			"id": row.tool_name,
			"name": row.tool_name,
			"description": row.description or "",
			"available": bool(row.available),
		}
		for row in getattr(doc, "tools", []) or []
	]
	return {
		"id": doc.name,
		"name": doc.name,
		"display_name": doc.connection_name or doc.name,
		"transport": str(doc.connection_type or "").lower(),
		"status": status,
		"status_message": doc.status_message or "",
		"tool_count": len([row for row in getattr(doc, "tools", []) or [] if row.available]) or _tool_count(doc.status_message),
		"tool_summaries": tool_summaries,
		"tool_summaries_available": bool(tool_summaries),
		"test_connection_supported": True,
	}


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


def _session_row(row: Any) -> dict[str, Any]:
	title = _display_title(row.name, row.title)
	return {
		"id": row.name,
		"name": row.name,
		"title": title,
		"preview": _summarize_text(title, limit=72),
		"modified": row.modified,
		"agent": getattr(row, "agent", None),
		"model": getattr(row, "model", None),
		"source": getattr(row, "source", None),
	}


def _attachment_row(row: Any) -> dict[str, Any]:
	return {
		"id": row.name,
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


def _transcript(doc, feedback_rows: list[dict[str, Any]], paused_run: dict[str, Any] | None) -> list[dict[str, Any]]:
	attachments_by_run: dict[str, list[dict[str, Any]]] = {}
	for row in getattr(doc, "attachments", []) or []:
		attachments_by_run.setdefault(row.run, []).append(
			{"file_name": row.file_name, "file_size": row.file_size}
		)
	feedback_by_run = {row["run"]: row for row in feedback_rows}
	questions_by_run = {paused_run["run"]: paused_run["questions"]} if paused_run else {}
	transcript: list[dict[str, Any]] = []
	assistant_by_run: dict[str, dict[str, Any]] = {}

	for row in getattr(doc, "messages", []) or []:
		if row.role == "user":
			transcript.append(
				{
					"id": row.name,
					"role": "user",
					"content": row.content,
					"run": row.run,
					"attachments": attachments_by_run.get(row.run, []),
				}
			)
			continue

		if row.role == "assistant":
			entry = {
				"id": row.name,
				"role": "assistant",
				"run": row.run,
				"content": row.content or "",
				"executions": [],
				"questions": questions_by_run.get(row.run, []),
				"feedback": feedback_by_run.get(row.run),
				"pending": False,
			}
			for call in _parse_json(row.tool_calls, []):
				raw_name = call.get("function", {}).get("name") or ""
				identity = _tool_identity(raw_name)
				arguments = _parse_json(call.get("function", {}).get("arguments"), {})
				entry["executions"].append(
					{
						"id": call.get("id"),
						"kind": identity["kind"],
						"tool_name": identity["tool_name"],
						"display_title": identity["tool_name"],
						"connection_name": identity["connection_name"],
						"status": "running",
						"duration_ms": None,
						"input_summary": _input_summary(arguments),
						"result_summary": "",
						"raw_input": arguments,
						"raw_output": None,
						"error": None,
						"approval_status": None,
					}
				)
			transcript.append(entry)
			if row.run:
				assistant_by_run[row.run] = entry
			continue

		if row.role == "tool":
			assistant = assistant_by_run.get(row.run)
			if not assistant:
				continue
			execution = next(
				(item for item in assistant["executions"] if item["id"] == row.tool_call_id),
				None,
			)
			if not execution:
				continue
			execution["raw_output"] = row.content
			execution["result_summary"] = _result_summary(row.content)
			parsed = _parse_json(row.content, {})
			if isinstance(parsed, dict):
				status = parsed.get("status")
				if status == "denied":
					execution["approval_status"] = "denied"
				elif status == "redirect":
					execution["approval_status"] = "redirected"
				elif status == "approved":
					execution["approval_status"] = "approved"
				if parsed.get("error"):
					execution["status"] = "error"
					execution["error"] = parsed.get("error")
				else:
					execution["status"] = "completed"
			else:
				execution["status"] = "completed"

	for item in transcript:
		if item["role"] == "assistant" and item["questions"]:
			for execution in item["executions"]:
				question = next((q for q in item["questions"] if q.get("key") == execution["id"]), None)
				if question:
					execution["status"] = "awaiting_confirmation"
	return transcript


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
