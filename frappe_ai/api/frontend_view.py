# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Pure presentation helpers for the frontend JSON API."""

from __future__ import annotations

import json
from typing import Any


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
