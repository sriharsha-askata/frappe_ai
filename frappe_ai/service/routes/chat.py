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
	"""Forwards Agno's own exception logging into Frappe's Error Log for one run.

	Agno's `"agno"` logger sets `propagate = False` and its `RichHandler` has
	`rich_tracebacks=False` with a bare `"%(message)s"` format, so the real
	exception behind an opaque `RunErrorEvent` (see `agno/agents/base.py`'s
	`except Exception as e: log_exception(...)`) is otherwise dropped — never
	reaching this process's own logger hierarchy, let alone Frappe. Attaching
	directly to the `"agno"` logger (bypassing propagate) for the duration of one
	`agent.arun()` call, with a formatter that renders `exc_info`, recovers it.
	"""

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
	model_config: dict[str, Any] | None = None
	try:
		agent, config = await builder.build(run, user, approved_call_ids=approved_ids)
		model_config = config.get("model") or {}
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


def _to_agno_messages(
	messages: list[dict[str, Any]],
	*,
	questions_by_id: dict[str, dict[str, Any]],
	redirect_answers: dict[str, str],
	approved_results: list[dict[str, Any]] | None = None,
) -> list[Message]:
	"""Convert Frappe's stored transcript (OpenAI-format dicts) to Agno `Message`s
	for this turn's model call.

	The stored `system` row is preserved. Some OpenAI-compatible providers reject
	Agno's provider-specific `developer` role for agent instructions, so
	`AgentBuilder` leaves `instructions=None` and relies on the transcript's
	`system` message instead.

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
		tool_row = {"role": "tool", "tool_call_id": r["id"], "content": json.dumps(r["result"], default=str)}
		_trim_stored_tool_content(tool_row)
		out.append(tool_row)
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
