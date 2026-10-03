# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Run an Agno turn and buffer its SSE events and diagnostics."""

from __future__ import annotations

import json
import logging
import time
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
	ToolCallCompletedEvent,
	ToolCallStartedEvent,
)

from frappe_ai.service.builder import PendingConfirmation
from frappe_ai.service.frappe_client import FrappeClient
from frappe_ai.service.routes.confirmation import _pending_from_tool_execution


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
