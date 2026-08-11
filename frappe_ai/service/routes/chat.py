# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""SSE chat route — replaces `main.py`'s Phase 2 `/stream/{run}` placeholder.

Implements `001-architecture.md` §5.1's streaming half (run-token verification was
already correct in the Phase 2 placeholder; this fills in the actual Agno run loop
and event translation) and §5.2's confirmation pause/resume, and §9's failure
handling (client disconnect → `fail_run`).

**Event mapping.** Agno's streaming events (`agno.run.agent`) are translated to the
wire format fixed by `001-architecture.md` §8 — `run_started`/`text`/`tool_started`/
`tool_ended`/`error`/`done` — so the Phase 6 Vue panel port stays mechanical, exactly
as that spec requires. Agno's own event vocabulary is deliberately not exposed
verbatim; `_translate_event` is the one place that mapping lives.

**Pause is never Agno-native** (see `service/builder.py`'s module docstring for why):
a Paused segment here means one or more tool calls raised `PendingConfirmation`
during this turn's Agno run, not that Agno itself entered `RunStatus.paused`. The
loop catches those, stops calling the model further, and emits `done` with
`status: "Paused"` and a `questions` payload shaped like `flow`'s
(`{key, name, arguments, prompt}` per pending call) — `key` is the tool call id
`resume_run`'s `answers` dict is keyed by, matching `001-architecture.md` §5.2's
Approve/Deny/redirect semantics exactly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from agno.models.message import Message
from agno.run.agent import (
	RunContentEvent,
	RunErrorEvent,
	RunOutput,
	RunStatus,
	ToolCallCompletedEvent,
	ToolCallStartedEvent,
)

from frappe_ai.service.builder import PENDING_CONFIRMATION_MARKER, AgentBuilder, AgentBuildError, PendingConfirmation
from frappe_ai.service.frappe_client import FrappeClient, FrappeClientError

logger = logging.getLogger("frappe_ai.service.chat")


async def stream_chat(
	run: str,
	user: str,
	session: str,
	frappe_client: FrappeClient,
	*,
	answers: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
	"""Run one turn (fresh or resumed) and yield SSE frames.

	Args:
		run (str): `AI Run` name.
		user (str): Acting user — threaded through to every tool dispatch.
		session (str): `AI Session` name (only used for the `run_started` frame).
		frappe_client (FrappeClient): Shared client for config fetch, dispatch, and
			the persistence callbacks.
		answers (dict[str, Any] | None): On resume, maps pending tool-call ids to
			"Approve" / "Deny" / free-text redirect feedback. `None` on a fresh turn.

	Yields:
		bytes: SSE frames (`event: ...\\ndata: ...\\n\\n`), in the wire format fixed
			by `001-architecture.md` §8.
	"""
	yield _frame("run_started", {"run": run, "session": session})

	builder = AgentBuilder(frappe_client)
	approved_ids, denied = _split_answers(answers)

	persisted = False
	try:
		agent, config = await builder.build(run, user, approved_call_ids=approved_ids)
		questions_by_id = {q["key"]: q for q in (config.get("questions") or [])}

		if denied:
			# ADR/§5.2: a Deny on any pending call halts the whole run immediately —
			# no further model call, matching flow's _has_denial short-circuit.
			result = _denied_result(config["messages"], questions_by_id, denied)
			await frappe_client.persist_run_result(run, result)
			persisted = True
			yield _frame("done", _done_payload(result))
			return

		approved_results: list[dict[str, Any]] = []
		if approved_ids:
			# Resume must actually run each approved call, not just permit it —
			# discovered live: `approved_call_ids` alone only gates whether a call
			# the *model* re-requests dispatches; nothing previously made the model
			# re-request the exact call it was blocked on (the pending assistant
			# tool_calls message is deliberately not replayed to the model — see
			# `_messages_excluding_pending`'s docstring — so there is nothing left
			# to prompt a re-request). flow's own resume (`_prepare_resume`:
			# "Approve" → actually run the tool and serialize the result) does this
			# directly rather than hoping the model asks again; this must match
			# that. Pending-call arguments come from `config["questions"]` —
			# `AI Run.questions` as persisted by the prior (Paused) segment.
			approved_results = await _dispatch_approved(frappe_client, user, questions_by_id, approved_ids)
			for r in approved_results:
				yield _frame("tool_started", {"id": r["id"], "name": r["name"], "arguments": r["arguments"]})
				yield _frame("tool_ended", {"id": r["id"], "name": r["name"], "result": r["result"]})

		messages = _to_agno_messages(
			config["messages"],
			questions_by_id=questions_by_id,
			redirect_answers=_redirects(answers),
			approved_results=approved_results,
		)
		pending: list[PendingConfirmation] = []
		final_output: RunOutput | None = None
		run_error: str | None = None

		async for event in agent.arun(input=messages, stream=True, stream_events=True, yield_run_output=True):
			if isinstance(event, RunOutput):
				final_output = event
				continue
			if isinstance(event, RunErrorEvent):
				# The model call itself failed (bad credentials, provider outage,
				# etc.) — on this failure path Agno's generator yields a
				# RunErrorEvent and never yields a final RunOutput at all (verified
				# empirically: a 401 from the provider produces
				# RunStartedEvent -> ModelRequestStartedEvent -> RunErrorEvent, no
				# RunOutput), so this can't be detected via final_output.status
				# alone the way a mid-run error might be.
				run_error = event.content or "Model call failed."
				continue
			if isinstance(event, RunContentEvent) and event.content:
				yield _frame("text", {"content": event.content})
			elif isinstance(event, ToolCallStartedEvent) and event.tool:
				yield _frame(
					"tool_started",
					{"id": event.tool.tool_call_id, "name": event.tool.tool_name, "arguments": event.tool.tool_args},
				)
			elif isinstance(event, ToolCallCompletedEvent) and event.tool:
				maybe_pending = _pending_from_tool_execution(event.tool) if event.tool.tool_call_error else None
				if maybe_pending:
					pending.append(maybe_pending)
				else:
					yield _frame(
						"tool_ended",
						{"id": event.tool.tool_call_id, "name": event.tool.tool_name, "result": event.content},
					)

		if run_error is None and final_output is not None and final_output.status == RunStatus.error:
			run_error = final_output.content or "Model call failed."

		if run_error is not None:
			# Agno reports model/run failures via a RunErrorEvent (and/or
			# RunOutput.status) rather than raising out of arun(), so this isn't
			# caught by the except clauses below. Treat it the same as any other
			# run-ending failure: fail_run, not persist_run_result.
			await frappe_client.fail_run(run, str(run_error))
			persisted = True
			yield _frame("error", {"message": str(run_error)})
			return

		result = _build_result(final_output, pending, approved_results=approved_results)
		await frappe_client.persist_run_result(run, result)
		persisted = True
		yield _frame("done", _done_payload(result))

	except (AgentBuildError, FrappeClientError) as e:
		logger.exception("Chat stream failed for run %s", run)
		if not persisted:
			try:
				await frappe_client.fail_run(run, str(e))
			except FrappeClientError:
				pass
		yield _frame("error", {"message": str(e)})
	except GeneratorExit:
		# Client disconnected mid-stream. No `done` was ever produced — fail the run
		# rather than leave it Running (001-architecture.md §9). Best-effort: the
		# client is already gone, there's nothing to yield to.
		if not persisted:
			try:
				await frappe_client.fail_run(run, "Stream interrupted: client disconnected.")
			except FrappeClientError:
				pass
		raise
	except Exception as e:
		logger.exception("Unexpected error in chat stream for run %s", run)
		if not persisted:
			try:
				await frappe_client.fail_run(run, str(e))
			except FrappeClientError:
				pass
		yield _frame("error", {"message": str(e)})


def _frame(event: str, payload: dict[str, Any]) -> bytes:
	data = json.dumps({"type": event, **payload}, default=str)
	return f"event: {event}\ndata: {data}\n\n".encode()


def _to_agno_messages(
	messages: list[dict[str, Any]],
	*,
	questions_by_id: dict[str, dict[str, Any]],
	redirect_answers: dict[str, str],
	approved_results: list[dict[str, Any]] | None = None,
) -> list[Message]:
	"""Convert Frappe's stored transcript (OpenAI-format dicts) to Agno `Message`s
	for this turn's model call.

	The stored `system` row is dropped — `AgentBuilder` already passes the agent's
	`instructions` to `Agent(instructions=...)`, which builds Agno's own system
	message; including both would duplicate it.

	On resume, neither the assistant tool-call request nor its result for a
	previously-pending call is part of the stored transcript `messages` came from
	(`_messages_excluding_pending` deliberately strips both from what gets
	persisted — see its docstring for why). Both `approved_results` (a real
	dispatch result) and `redirect_answers` (free-text feedback) therefore need the
	matching assistant `tool_calls` request reconstructed here, fresh, from
	`questions_by_id` (`config["questions"]`, keyed by call id) — for this turn's
	model call only, never persisted. Without the paired assistant message, a
	bare `role: "tool"` response has no request to answer and most model APIs
	reject it as malformed.
	"""
	out: list[Message] = []
	for m in messages:
		if m.get("role") == "system":
			continue
		out.append(Message(role=m["role"], content=m.get("content"), tool_call_id=m.get("tool_call_id")))
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


async def _dispatch_approved(
	frappe_client: FrappeClient,
	user: str,
	questions_by_id: dict[str, dict[str, Any]],
	approved_ids: frozenset[str],
) -> list[dict[str, Any]]:
	"""Actually run each approved pending call, using the arguments it was
	originally paused with.

	This is the piece `approved_call_ids` on `AgentBuilder`/`Function.entrypoint`
	alone doesn't cover: that flag only lets a call *the model re-requests* through
	without raising `PendingConfirmation` again — it does nothing to make the model
	re-request the exact call it was blocked on, and after `_messages_excluding_pending`
	strips the pending assistant `tool_calls` message from the stored transcript,
	there is nothing left to prompt a re-request either. Dispatching directly here,
	from the arguments `AI Run.questions` already recorded, matches `flow`'s own
	`_prepare_resume` ("Approve" → actually run the tool and serialize the result).

	Args:
		frappe_client (FrappeClient): Used for the actual dispatch calls.
		user (str): Acting user — same identity every other dispatch in this run uses.
		questions_by_id (dict[str, dict[str, Any]]): `config["questions"]` keyed by
			call id — each `{key, name, arguments, prompt}`.
		approved_ids (frozenset[str]): Call ids answered `"Approve"` this resume.

	Returns:
		list[dict[str, Any]]: `[{"id", "name", "arguments", "result"}, ...]` for
			every approved id found in `questions_by_id` (silently skips an approved
			id with no matching question — stale/mismatched answers, not this run's
			own pending calls).
	"""
	results: list[dict[str, Any]] = []
	for call_id in approved_ids:
		question = questions_by_id.get(call_id)
		if question is None:
			continue
		response = await frappe_client.dispatch_tool(question["name"], user, question.get("arguments") or {})
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
	"""Recover a `PendingConfirmation` from a failed tool call, if that's what failed.

	Agno's `Function.aexecute` catches `PendingConfirmation` (a plain `Exception`,
	deliberately not `agno.exceptions.AgentRunException` — see that class's
	docstring) as a generic tool failure: `ToolExecution.tool_call_error = True` and
	the completed event's `content` becomes `str(exception)`, which
	`PendingConfirmation.__str__` formats as a colon-separated string starting with
	`PENDING_CONFIRMATION_MARKER`. Any other tool error has a different `content`
	and is a genuine failure, not a pause — returns `None` for those so the caller
	surfaces them as a normal `tool_ended` with the error in `result`.
	"""
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
	"""Build the dict `AIRun.apply_result` expects, from this segment's Agno output
	plus any tool calls that require confirmation.

	`approved_results` (resume only) must be **prepended** to whatever gets
	persisted, not just injected into the model's input — discovered live: Agno's
	`RunOutput.messages` only contains messages it generated *during* this
	`arun()` call, not the extra assistant/tool message pairs `_to_agno_messages`
	injected into `input` to give the model context. Without prepending them here
	too, the approved call's real result would show up as a `role: "tool"` row
	with no matching assistant `tool_calls` request anywhere in the stored
	transcript — invalid shape for a future resume/continuation of this session
	to replay to the model.
	"""
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
		out.append({"role": "tool", "tool_call_id": r["id"], "content": json.dumps(r["result"], default=str)})
	return out


def _denied_result(
	prior_messages: list[dict[str, Any]],
	questions_by_id: dict[str, dict[str, Any]],
	denied_ids: list[str],
) -> dict[str, Any]:
	"""A Deny on any pending call halts the run — terminal Completed with no further
	model call, matching `001-architecture.md` §5.2's Deny semantics.

	`AIRun.apply_result` documents `messages` as the segment's **full** transcript,
	diffing it against what the session already has stored to compute the delta
	(`_new_messages_for_session`) — discovered live: an earlier version of this
	function returned only the denial rows themselves (already just a delta), which
	`_new_messages_for_session` then re-sliced against the existing count and
	silently dropped entirely, since a 1-row list sliced from index 2 is empty.

	Each denied call's assistant `tool_calls` request was stripped from the stored
	transcript on pause (`_messages_excluding_pending`), so — same reasoning as
	`_approved_result_messages` — it must be reconstructed here from
	`questions_by_id` before the paired `role: "tool"` denial row is persisted, or
	the denial row is left orphaned with no request to answer.

	`prior_messages` is passed through **unfiltered** (system row included) — a
	second live-testing find: `_new_messages_for_session`'s delta slice
	(`full_transcript[existing_count:]`) counts against exactly what's stored in
	`AI Session Message`, which does include the system row. Filtering it out here
	shifted every index by one and silently dropped the reconstructed assistant
	message from what got persisted (only the denial's `tool` row survived the
	slice) — caught only by inspecting the DB after a live Deny, not by unit tests,
	since none of them round-tripped a Deny through the real accumulate-by-count
	delta logic in `AIRun._new_messages_for_session`.
	"""
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
	Phase 6 may route this through a dedicated Frappe call if richer prompts matter."""
	return f"Run {pending.name} with {json.dumps(pending.arguments, default=str)}?"


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
		out.append(row)
	return out


def _messages_excluding_pending(
	output: RunOutput | None, pending: list[PendingConfirmation]
) -> list[dict[str, Any]]:
	"""Same as `_messages`, but strips the never-actually-run attempt from what gets
	persisted on a Paused segment.

	A tool call caught as `PendingConfirmation` still produces a `role: "tool"`
	message in Agno's transcript (Agno's own error-handling path — see
	`PendingConfirmation`'s docstring), whose `content` is the internal
	`PENDING_CONFIRMATION_MARKER` string, not a real result. Persisting that
	verbatim — discovered live, not by unit tests, since the unit tests never
	round-tripped a paused run's transcript back through a second real model call
	— poisons the durable session transcript two ways on resume:

	1. `AISession.build_prompt_messages()` replays it to the model as if it were a
	   genuine tool result, which the model then treats as "this call already
	   happened" rather than "this call is still awaiting approval" — observed
	   live as the model fabricating a plausible-looking fake success instead of
	   actually retrying the call after being told it was approved.
	2. Most model APIs require every assistant `tool_calls` entry to have a
	   matching tool-role response in the same request; leaving the marker in
	   place technically satisfies that shape while being semantically wrong.

	Instead: drop each pending call's marker tool-message, and drop the assistant
	message that requested it if that assistant turn only ever requested pending
	(never-approved, never-executed) calls. The persisted transcript then ends
	cleanly at the last real exchange; resume rebuilds the tool-call request from
	scratch via a fresh model turn rather than replaying a stale, unresolved one.
	"""
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


def _done_payload(result: dict[str, Any]) -> dict[str, Any]:
	payload = {
		"status": result["status"],
		"iterations": result["iterations"],
		"output": result["output"],
		"usage": result["usage"],
	}
	if result.get("questions"):
		payload["questions"] = result["questions"]
	return payload
