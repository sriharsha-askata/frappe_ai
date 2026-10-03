# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Convert and serialize Agno messages for stored run transcripts."""

from __future__ import annotations

import json
from typing import Any

from agno.models.message import Message
from agno.run.agent import RunOutput

MAX_STORED_TOOL_CONTENT_CHARS = 8000


def _repair_orphaned_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Insert assistant requests before orphaned tool results in stored history.

	This repairs transcripts that would otherwise be rejected by model APIs."""
	known_ids: set[str] = set()
	for m in messages:
		for tc in m.get("tool_calls") or []:
			cid = tc.get("id") or tc.get("tool_call_id") or ""
			if cid:
				known_ids.add(cid)

	out: list[dict[str, Any]] = []
	for m in messages:
		if m.get("role") == "tool":
			cid = m.get("tool_call_id")
			if cid and cid not in known_ids:
				out.append({
					"role": "assistant",
					"content": None,
					"tool_calls": [{
						"id": cid,
						"type": "function",
						"function": {"name": "tool", "arguments": "{}"},
					}],
				})
				known_ids.add(cid)
		out.append(m)
	return out


def _to_agno_messages(
	messages: list[dict[str, Any]],
	*,
	questions_by_id: dict[str, dict[str, Any]],
	redirect_answers: dict[str, str],
	approved_results: list[dict[str, Any]] | None = None,
) -> list[Message]:
	"""Convert the stored transcript to Agno messages for this model turn.

	Keep the stored system message. On resume, reconstruct each pending assistant
	tool request before its approved result or redirect feedback; most model APIs
	reject a tool response without its matching request."""
	messages = _repair_orphaned_tool_results(messages)
	out: list[Message] = []
	for m in messages:
		out.append(Message(
			role=m["role"],
			content=m.get("content"),
			tool_call_id=m.get("tool_call_id"),
			tool_calls=m.get("tool_calls"),
		))
	for r in approved_results or []:
		out.append(_reconstructed_tool_call_message(r["id"], r["name"], r["arguments"]))
		out.append(Message(role="tool", tool_call_id=r["id"], content=json.dumps(r["result"], default=str)))
	for call_id, feedback in redirect_answers.items():
		question = questions_by_id.get(call_id)
		if question is None:
			continue
		out.append(_reconstructed_tool_call_message(call_id, question["name"], question.get("arguments") or {}))
		out.append(
			Message(
				role="tool",
				tool_call_id=call_id,
				content=json.dumps(
					{
						"status": "redirect",
						"user_feedback": feedback,
						"instruction": "Adjust your approach based on this feedback and retry if appropriate.",
					}
				),
			)
		)
	return out


def _reconstructed_tool_call_message(call_id: str, name: str, arguments: dict[str, Any]) -> Message:
	"""Build the assistant `tool_calls` message a resumed call's tool-result message
	needs paired with it, for this turn's model call only (see `_to_agno_messages`)."""
	return Message(
		role="assistant",
		content=None,
		tool_calls=[
			{
				"id": call_id,
				"type": "function",
				"function": {"name": name, "arguments": json.dumps(arguments, default=str)},
			}
		],
	)


def _iterations(output: RunOutput | None) -> int:
	if output is None or not output.messages:
		return 0
	return sum(1 for m in output.messages if getattr(m, "role", None) == "assistant")


def _tool_calls(output: RunOutput | None) -> list[dict[str, Any]]:
	if output is None or not output.tools:
		return []
	return [{"id": t.tool_call_id, "name": t.tool_name, "arguments": t.tool_args} for t in output.tools]


def _usage(output: RunOutput | None) -> dict[str, int]:
	if output is None or output.metrics is None:
		return {}
	m = output.metrics
	return {
		"prompt_tokens": m.input_tokens or 0,
		"completion_tokens": m.output_tokens or 0,
		"total_tokens": m.total_tokens or 0,
	}


def _messages(output: RunOutput | None) -> list[dict[str, Any]]:
	if output is None or not output.messages:
		return []
	out = []
	for m in output.messages:
		if getattr(m, "role", None) == "system":
			continue
		row: dict[str, Any] = {"role": m.role, "content": m.content}
		if getattr(m, "tool_call_id", None):
			row["tool_call_id"] = m.tool_call_id
		if getattr(m, "tool_calls", None):
			row["tool_calls"] = m.tool_calls
		_trim_stored_tool_content(row)
		out.append(row)
	return out


def _trim_stored_tool_content(row: dict[str, Any]) -> None:
	if row.get("role") != "tool" or not isinstance(row.get("content"), str):
		return
	content = row["content"]
	if len(content) <= MAX_STORED_TOOL_CONTENT_CHARS:
		return
	row["content"] = (
		content[:MAX_STORED_TOOL_CONTENT_CHARS]
		+ f"\n\n[truncated {len(content) - MAX_STORED_TOOL_CONTENT_CHARS} chars from stored tool output]"
	)
