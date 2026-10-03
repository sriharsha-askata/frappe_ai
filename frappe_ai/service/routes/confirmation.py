# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Handle confirmation answers and persistable pause or resume results."""

from __future__ import annotations

import json
from typing import Any

from agno.run.agent import RunOutput

from frappe_ai.service.builder import PENDING_CONFIRMATION_MARKER, PendingConfirmation
from frappe_ai.service.frappe_client import FrappeClient
from frappe_ai.service.routes.transcript import (
	_iterations,
	_messages,
	_tool_calls,
	_trim_stored_tool_content,
	_usage,
)


async def _dispatch_approved(
	frappe_client: FrappeClient,
	user: str,
	questions_by_id: dict[str, dict[str, Any]],
	approved_ids: frozenset[str],
	run: str,
	tool_sources: dict[str, str | None],
) -> list[dict[str, Any]]:
	"""Execute approved pending calls using their recorded arguments before resuming.

	Approval alone does not make the model re-request a pending call. Pass `run` for
	budget enforcement and choose the dispatcher from the bound tool's source:
	Assistant Core tools require `dispatch_plugin_tool` for FAC permissions and
	server-owned context. Skip approvals with no matching question or bound tool."""
	results: list[dict[str, Any]] = []
	for call_id in approved_ids:
		question = questions_by_id.get(call_id)
		if question is None:
			continue
		if question["name"] not in tool_sources:
			# The tool was unbound from the agent between pause and resume, so this
			# run may no longer call it — an approval cannot re-grant what the
			# current config withholds.
			continue
		dispatch = (
			frappe_client.dispatch_plugin_tool
			if tool_sources[question["name"]] == "fac"
			else frappe_client.dispatch_tool
		)
		response = await dispatch(question["name"], user, question.get("arguments") or {}, run, call_id)
		result = response.get("result") if "error" not in response else {"error": response["error"]}
		results.append({"id": call_id, "name": question["name"], "arguments": question.get("arguments") or {}, "result": result})
	return results


def _split_answers(answers: dict[str, Any] | None) -> tuple[frozenset[str], list[str]]:
	"""Split a resume's answers into (approved call ids, denied call ids)."""
	if not answers:
		return frozenset(), []
	approved = frozenset(k for k, v in answers.items() if v == "Approve")
	denied = [k for k, v in answers.items() if v == "Deny"]
	return approved, denied


def _redirects(answers: dict[str, Any] | None) -> dict[str, str]:
	if not answers:
		return {}
	return {k: v for k, v in answers.items() if v not in ("Approve", "Deny")}


def _pending_from_tool_execution(tool) -> PendingConfirmation | None:
	"""Recover `PendingConfirmation` from Agno's failed-tool marker.

	Return `None` for ordinary tool errors so they remain tool results."""
	content = getattr(tool, "result", None)
	if not isinstance(content, str) or not content.startswith(PENDING_CONFIRMATION_MARKER):
		return None
	_marker, call_id, name, arguments_json = content.split(":", 3)
	return PendingConfirmation(tool_call_id=call_id, name=name, arguments=json.loads(arguments_json))


def _build_result(
	final_output: RunOutput | None,
	pending: list[PendingConfirmation],
	*,
	approved_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
	"""Build the segment result expected by `AIRun.apply_result`.

	Prepend approved-call assistant/tool pairs to the persisted transcript: Agno's
	`RunOutput.messages` omits messages supplied as input to this model turn."""
	prefix = _approved_result_messages(approved_results)

	if pending:
		return {
			"status": "Paused",
			"iterations": _iterations(final_output),
			"output": None,
			"tool_calls": _tool_calls(final_output),
			"questions": [
				{"key": p.tool_call_id, "name": p.name, "arguments": p.arguments, "prompt": _confirm_prompt(p)}
				for p in pending
			],
			"usage": _usage(final_output),
			"messages": prefix + _messages_excluding_pending(final_output, pending),
		}

	return {
		"status": "Completed",
		"iterations": _iterations(final_output),
		"output": final_output.content if final_output else None,
		"tool_calls": _tool_calls(final_output),
		"questions": None,
		"usage": _usage(final_output),
		"messages": prefix + _messages(final_output),
	}


def _approved_result_messages(approved_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
	"""The assistant tool_calls request + real tool result for each approved call,
	in Frappe's stored-transcript dict shape — see `_build_result`'s docstring."""
	out: list[dict[str, Any]] = []
	for r in approved_results or []:
		out.append(
			{
				"role": "assistant",
				"content": None,
				"tool_calls": [
					{
						"id": r["id"],
						"type": "function",
						"function": {"name": r["name"], "arguments": json.dumps(r["arguments"], default=str)},
					}
				],
			}
		)
		tool_row = {"role": "tool", "tool_call_id": r["id"], "content": json.dumps(r["result"], default=str)}
		_trim_stored_tool_content(tool_row)
		out.append(tool_row)
	return out


def _denied_result(
	prior_messages: list[dict[str, Any]],
	questions_by_id: dict[str, dict[str, Any]],
	denied_ids: list[str],
) -> dict[str, Any]:
	"""End the run after a denial without another model call.

	Include the complete prior transcript, including the system row, so
	`AIRun.apply_result` can append the correct delta. Reconstruct each denied
	call's assistant request before its tool response to keep the transcript valid."""
	prior = list(prior_messages)
	denial_rows: list[dict[str, Any]] = []
	for call_id in denied_ids:
		question = questions_by_id.get(call_id)
		if question is not None:
			denial_rows.append(
				{
					"role": "assistant",
					"content": None,
					"tool_calls": [
						{
							"id": call_id,
							"type": "function",
							"function": {
								"name": question["name"],
								"arguments": json.dumps(question.get("arguments") or {}, default=str),
							},
						}
					],
				}
			)
		denial_rows.append(
			{"role": "tool", "tool_call_id": call_id, "content": json.dumps({"status": "denied"})}
		)
	return {
		"status": "Completed",
		"iterations": 0,
		"output": None,
		"tool_calls": [],
		"questions": None,
		"usage": {},
		"messages": prior + denial_rows,
	}


def _confirm_prompt(pending: PendingConfirmation) -> str:
	"""Best-effort human-readable summary. `AI Tool.confirm_prompt` lambdas
	(`frappe_ai.tools.builtins`) run in Frappe, not here — the service only sees the
	JSON Schema, not the tool's Python. A generic fallback keeps the panel usable;
	The prompt uses the stored tool name and arguments."""
	return f"Run {pending.name} with {json.dumps(pending.arguments, default=str)}?"


def _messages_excluding_pending(
	output: RunOutput | None, pending: list[PendingConfirmation]
) -> list[dict[str, Any]]:
	"""Remove unexecuted pending calls from a paused segment's transcript.

	Agno emits a tool response containing `PENDING_CONFIRMATION_MARKER`; it is not
	a real result and must not be replayed. Drop that response and any assistant
	request consisting only of pending calls. Resume reconstructs the request from
	the stored questions."""
	pending_ids = {p.tool_call_id for p in pending}
	rows = _messages(output)

	kept: list[dict[str, Any]] = []
	for row in rows:
		if row.get("role") == "tool" and row.get("tool_call_id") in pending_ids:
			continue
		if row.get("role") == "assistant" and row.get("tool_calls"):
			calls = row["tool_calls"]
			requested_ids = {c.get("id") for c in calls if isinstance(c, dict)}
			if requested_ids and requested_ids <= pending_ids:
				continue
		kept.append(row)
	return kept
