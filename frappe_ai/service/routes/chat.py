# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Run Agno turns and translate them to the six SSE events in architecture §8.

Confirmation pauses use `PendingConfirmation` and persist questions keyed by
tool-call ID. Disconnects and errors call `fail_run`."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agno.agent import Agent
from agno.models.message import Message
from agno.run.agent import (
	ModelRequestCompletedEvent,
	ModelRequestStartedEvent,
	RunContentEvent,
	RunErrorEvent,
	RunOutput,
	RunStatus,
	ToolCallCompletedEvent,
	ToolCallStartedEvent,
)

from frappe_ai.lib.model import normalize_provider_error
from frappe_ai.service.builder import PENDING_CONFIRMATION_MARKER, AgentBuilder, AgentBuildError, PendingConfirmation
from frappe_ai.service.frappe_client import FrappeClient, FrappeClientError

logger = logging.getLogger("frappe_ai.service.chat")
MAX_STORED_TOOL_CONTENT_CHARS = 8000

def _exception_is_stream_timeout(exc: BaseException) -> bool:
	current: BaseException | None = exc
	for _ in range(8):
		if current is None:
			break
		name = type(current).__name__
		if name in {"ReadTimeout", "WriteTimeout", "ConnectTimeout", "TimeoutError", "APITimeoutError"}:
			return True
		if "deadline exceeded" in str(current).lower():
			return True
		current = current.__cause__ or current.__context__
	return False

class _AgnoDiagnosticHandler(logging.Handler):
	"""Forward Agno exceptions to Frappe's Error Log for one run.

	Attach to Agno's logger directly because it does not propagate exceptions."""

	def __init__(
		self, frappe_client: FrappeClient, run: str, level: int = logging.WARNING, title: str = "Agno error"
	) -> None:
		super().__init__(level=level)
		self._frappe_client = frappe_client
		self._run = run
		self._title = title
		self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

	def emit(self, record: logging.LogRecord) -> None:
		import asyncio

		message = self.format(record)[:4000]
		task = asyncio.create_task(
			self._frappe_client.log_diagnostic(f"frappe_ai Run {self._run}: {self._title}", message)
		)
		# Best-effort: don't let a logging failure raise into Agno's own exception path.
		task.add_done_callback(lambda t: t.exception())


@dataclass
class _TurnAttempt:
	"""One `agent.arun()` attempt's outcome, buffered rather than yielded directly.

	Buffering (instead of yielding SSE frames as events arrive) is what makes the
	fallback-model retry in `stream_chat` safe: a failed attempt's frames are
	simply discarded rather than having already reached the client, so retrying
	the whole turn from scratch on a different model can't emit duplicates.
	"""

	frames: list[bytes] = field(default_factory=list)
	pending: list[PendingConfirmation] = field(default_factory=list)
	final_output: RunOutput | None = None
	run_error: Any | None = None
	model_call_index: int = 0
	call_log: list[str] = field(default_factory=list)
	tool_completed: bool = False
	tool_log: list[str] = field(default_factory=list)


async def _run_agent_turn(agent: Agent, messages: list[Message], run: str, frappe_client: FrappeClient) -> _TurnAttempt:
	"""Run one `agent.arun()` turn to completion, buffering its SSE frames.

	`tool_completed` specifically tracks whether any tool call *finished*
	(dispatched and returned, side effects included) — not merely started — since
	that's the fact that makes a fallback-model retry of this same turn unsafe.
	"""
	attempt = _TurnAttempt()
	model_call_started_at: float | None = None

	model = getattr(agent, "model", None)
	orig_ainvoke_stream = getattr(model, "ainvoke_stream", None) if model is not None else None
	if orig_ainvoke_stream is not None:

		async def _ainvoke_stream_with_timeout_context(messages, *args, **kwargs):
			started = time.monotonic()
			chunk_count = 0
			try:
				async for chunk in orig_ainvoke_stream(messages, *args, **kwargs):
					chunk_count += 1
					yield chunk
			except Exception as exc:
				elapsed = round(time.monotonic() - started, 3)
				if _exception_is_stream_timeout(exc):
					from agno.exceptions import ModelProviderError

					raise ModelProviderError(
						message=(
							f"Provider stream timed out after {elapsed:.0f}s while reading the model "
							f"response ({chunk_count} chunks received). The model was still generating "
							"a tool call. Increase AI Settings.stream_timeout if this persists."
						),
						status_code=504,
						model_name=getattr(model, "name", None),
						model_id=getattr(model, "id", None),
					) from exc
				raise

		model.ainvoke_stream = _ainvoke_stream_with_timeout_context

	agno_diagnostic_handler = _AgnoDiagnosticHandler(frappe_client, run)
	agno_logger = logging.getLogger("agno")
	agno_logger.addHandler(agno_diagnostic_handler)
	try:
		async for event in agent.arun(input=messages, stream=True, stream_events=True, yield_run_output=True):
			if isinstance(event, ModelRequestStartedEvent):
				attempt.model_call_index += 1
				model_call_started_at = time.monotonic()
				continue
			if isinstance(event, ModelRequestCompletedEvent):
				duration = time.monotonic() - model_call_started_at if model_call_started_at is not None else None
				attempt.call_log.append(
					f"call #{attempt.model_call_index} duration={f'{duration:.2f}s' if duration is not None else 'n/a'} "
					f"input_tokens={event.input_tokens} output_tokens={event.output_tokens} "
					f"total_tokens={event.total_tokens} time_to_first_token={event.time_to_first_token}"
				)
				continue
			if isinstance(event, RunOutput):
				attempt.final_output = event
				continue
			if isinstance(event, RunErrorEvent):
				# The model call itself failed (bad credentials, provider outage,
				# etc.) — on this failure path Agno's generator yields a
				# RunErrorEvent and never yields a final RunOutput at all (verified
				# empirically: a 401 from the provider produces
				# RunStartedEvent -> ModelRequestStartedEvent -> RunErrorEvent, no
				# RunOutput), so this can't be detected via final_output.status
				# alone the way a mid-run error might be.
				attempt.run_error = event
				continue
			if isinstance(event, RunContentEvent) and event.content:
				attempt.frames.append(_frame("text", {"content": event.content}))
			elif isinstance(event, ToolCallStartedEvent) and event.tool:
				attempt.frames.append(
					_frame(
						"tool_started",
						{"id": event.tool.tool_call_id, "name": event.tool.tool_name, "arguments": event.tool.tool_args},
					)
				)
			elif isinstance(event, ToolCallCompletedEvent) and event.tool:
				attempt.tool_completed = True
				# event.content here is a short status string for the SSE tool_ended
				# frame (client display only) — the actual result fed back into the
				# model's own conversation is event.tool.result, which is what this
				# diagnostic needs to measure.
				result_text = str(event.tool.result) if event.tool.result is not None else ""
				attempt.tool_log.append(
					f"tool={event.tool.tool_name} args={event.tool.tool_args} "
					f"error={event.tool.tool_call_error} result_chars={len(result_text)} "
					f"result_preview={result_text[:200]!r}"
				)
				maybe_pending = _pending_from_tool_execution(event.tool) if event.tool.tool_call_error else None
				if maybe_pending:
					attempt.pending.append(maybe_pending)
				else:
					attempt.frames.append(
						_frame(
							"tool_ended",
							{"id": event.tool.tool_call_id, "name": event.tool.tool_name, "result": event.content},
						)
					)
	finally:
		agno_logger.removeHandler(agno_diagnostic_handler)

	return attempt


async def stream_chat(
	run: str,
	user: str,
	session: str,
	frappe_client: FrappeClient,
	*,
	answers: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
	"""Yield SSE frames for a fresh or resumed run.

	`answers` maps pending tool-call IDs to Approve, Deny, or redirect feedback.
	Tool dispatch and persistence use the acting `user`."""
	yield _frame("run_started", {"run": run, "session": session})

	builder = AgentBuilder(frappe_client)
	approved_ids, denied = _split_answers(answers)

	persisted = False
	model_config: dict[str, Any] | None = None
	try:
		agent, config = await builder.build(run, user, approved_call_ids=approved_ids)
		model_config = config.get("model") or {}
		questions_by_id = {q["key"]: q for q in (config.get("questions") or [])}

		if denied:
			# ADR/§5.2: a Deny on any pending call halts the whole run immediately —
			# no further model call after a denied pending tool.
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
			# to prompt a re-request). Dispatch approved calls directly and
			# serialize their results. Pending-call arguments come from `config["questions"]` —
			# `AI Run.questions` as persisted by the prior (Paused) segment.
			approved_results = await _dispatch_approved(
				frappe_client,
				user,
				questions_by_id,
				approved_ids,
				run,
				{tool["name"]: tool.get("source") for tool in (config.get("tools") or [])},
			)
			for r in approved_results:
				yield _frame("tool_started", {"id": r["id"], "name": r["name"], "arguments": r["arguments"]})
				yield _frame("tool_ended", {"id": r["id"], "name": r["name"], "result": r["result"]})

		messages = _to_agno_messages(
			config["messages"],
			questions_by_id=questions_by_id,
			redirect_answers=_redirects(answers),
			approved_results=approved_results,
		)
		attempt = await _run_agent_turn(agent, messages, run, frappe_client)

		# Retry once against the system default model if the *originally
		# configured* model failed before any tool call completed — i.e. no
		# side-effecting tool has run yet this turn, so a clean retry from
		# scratch on a different model can't double-run anything or emit
		# duplicate frames (the failed attempt's frames are discarded, not
		# yielded). If a tool already completed, retrying would risk re-running
		# side effects (e.g. persist_spec_review_result) — fail as before instead.
		#
		# In practice this guard rarely allows a retry: the observed failure
		# pattern is call #1 (context load) -> a tool call succeeds -> call #2
		# succeeds -> call #3 fails with an empty-message model_provider_error.
		# By the time the model fails, a tool has almost always already run, so
		# most model_provider_error failures are NOT eligible for fallback and
		# fail outright instead — confirmed via a temporary diagnostic during
		# testing (2026-08-29): tool_completed=True on every observed failure.
		if attempt.run_error is not None and not attempt.pending and not attempt.tool_completed:
			try:
				fallback_model_cfg = await frappe_client.get_fallback_model_config(user)
			except FrappeClientError:
				fallback_model_cfg = None
			if fallback_model_cfg and fallback_model_cfg.get("model_id") != config["model"].get("model_id"):
				await frappe_client.log_diagnostic(
					f"frappe_ai Run {run}: retrying with fallback model",
					f"Primary model {config['model'].get('model_id')} failed with "
					f"{type(attempt.run_error).__name__}: {attempt.run_error!r}. "
					f"Retrying with fallback model {fallback_model_cfg.get('model_id')}.",
				)
				fallback_agent = Agent(
					model=builder._build_model(fallback_model_cfg),
					name=agent.name,
					instructions=None,
					system_message_role="system",
					tools=agent.tools,
					markdown=agent.markdown,
					reasoning=agent.reasoning,
					db=None,
					add_history_to_context=False,
					telemetry=False,
				)
				attempt = await _run_agent_turn(fallback_agent, messages, run, frappe_client)

		for frame in attempt.frames:
			yield frame
		pending = attempt.pending
		final_output = attempt.final_output
		run_error = attempt.run_error
		model_call_index = attempt.model_call_index
		call_log = attempt.call_log
		tool_log = attempt.tool_log

		if run_error is None and final_output is not None and final_output.status == RunStatus.error:
			run_error = final_output.content or "Model call failed."

		if run_error is not None:
			error = normalize_provider_error(
				run_error,
				provider=config["model"].get("provider"),
				model_id=config["model"].get("model_id"),
			)
			await frappe_client.log_diagnostic(
				f"frappe_ai Run {run}: provider/model execution failed",
				"Run {run} failed after {n} model call(s): {msg}\n\n"
				"raw_error_type={etype}\nraw_error_repr={erepr!r}\n\nCall log:\n{calls}\n\nTool log:\n{tools}".format(
					run=run,
					n=model_call_index,
					msg=error.message,
					etype=type(run_error).__name__,
					erepr=run_error,
					calls="\n".join(call_log) or "(no completed calls)",
					tools="\n".join(tool_log) or "(no completed tool calls)",
				),
			)
			logger.error("Run %s failed in provider/model execution: %s", run, error.message)
			# Agno reports model/run failures via a RunErrorEvent (and/or
			# RunOutput.status) rather than raising out of arun(), so this isn't
			# caught by the except clauses below. Treat it the same as any other
			# run-ending failure: fail_run, not persist_run_result.
			await frappe_client.fail_run(run, error.message)
			persisted = True
			yield _frame("error", _error_payload(error))
			return

		result = _build_result(final_output, pending, approved_results=approved_results)
		await frappe_client.persist_run_result(run, result)
		persisted = True
		yield _frame("done", _done_payload(result))

	except (AgentBuildError, FrappeClientError) as e:
		logger.exception("Chat stream failed for run %s", run)
		error_message = str(e)
		error_code = getattr(e, "code", None)
		if not persisted:
			try:
				if model_config is not None:
					error_message = normalize_provider_error(
						e,
						provider=model_config.get("provider"),
						model_id=model_config.get("model_id"),
					).message
				await frappe_client.fail_run(run, error_message)
			except FrappeClientError:
				pass
		payload = {"message": error_message}
		if error_code:
			payload["code"] = error_code
		else:
			payload = _error_payload(
				normalize_provider_error(
					e,
					provider=(model_config or {}).get("provider"),
					model_id=(model_config or {}).get("model_id"),
				)
			)
		yield _frame("error", payload)
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
		error_message = str(e)
		if not persisted:
			try:
				if model_config is not None:
					error_message = normalize_provider_error(
						e,
						provider=model_config.get("provider"),
						model_id=model_config.get("model_id"),
					).message
				await frappe_client.fail_run(run, error_message)
			except FrappeClientError:
				pass
		normalized = normalize_provider_error(
			e,
			provider=(model_config or {}).get("provider"),
			model_id=(model_config or {}).get("model_id"),
		)
		yield _frame("error", _error_payload(normalized))


def _error_payload(error) -> dict[str, Any]:
	"""Keep normalized codes and Agno/provider diagnostics on the SSE wire."""
	payload = {
		"message": error.message,
		"code": error.code,
		"status_code": error.status_code,
		"retryable": error.retryable,
	}
	if error.diagnostics:
		payload["diagnostics"] = error.diagnostics
		for field_name in ("error_type", "error_id", "additional_data", "request_id", "body"):
			if field_name in error.diagnostics:
				payload[field_name] = error.diagnostics[field_name]
	return payload


def _frame(event: str, payload: dict[str, Any]) -> bytes:
	data = json.dumps({"type": event, **payload}, default=str)
	return f"event: {event}\ndata: {data}\n\n".encode()


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
