# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Run an Agno turn and stream its events to the client as SSE frames."""

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
	"""State of one `agent.arun()` attempt, filled in while its frames stream to the client.

	Frames leave as soon as Agno produces them. From the first one, `output_sent` is true and the
	attempt can no longer be replaced by a fallback-model retry: a retry would repeat what the
	client has already seen, and a tool may already have run."""

	pending: list[PendingConfirmation] = field(default_factory=list)
	final_output: RunOutput | None = None
	run_error: Any | None = None
	output_sent: bool = False
	model_call_index: int = 0
	model_call_started_at: float | None = None
	call_log: list[str] = field(default_factory=list)
	tool_log: list[str] = field(default_factory=list)


async def stream_agent_turn(
	agent: Agent, messages: list[Message], run: str, frappe_client: FrappeClient, attempt: _TurnAttempt
) -> AsyncIterator[bytes]:
	"""Run one `agent.arun()` turn, yielding each SSE frame the moment it is produced.

	The outcome (final output, error, pending confirmations, diagnostics) accumulates on `attempt`.
	Consume this under `contextlib.aclosing` so an early exit, such as a client disconnect, closes
	the Agno generator and removes the diagnostic log handler."""
	_report_stream_timeouts_clearly(agent)
	handler = _AgnoDiagnosticHandler(frappe_client, run)
	agno_logger = logging.getLogger("agno")
	agno_logger.addHandler(handler)
	events = agent.arun(input=messages, stream=True, stream_events=True, yield_run_output=True)
	try:
		async for event in events:
			frame = _frame_for_event(attempt, event)
			if frame is not None:
				attempt.output_sent = True
				yield frame
	finally:
		# Leaving the loop early does not close Agno's generator by itself; close it so its
		# own cleanup (open provider streams, tool calls) runs now instead of at garbage collection.
		close_events = getattr(events, "aclose", None)
		if close_events is not None:
			await close_events()
		agno_logger.removeHandler(handler)


def _report_stream_timeouts_clearly(agent: Agent) -> None:
	"""Wrap the model's streaming call so a stalled provider read becomes a readable error.

	An `httpx.ReadTimeout` stringifies as an empty message, which surfaced as an opaque
	`model_provider_error`; this names the elapsed time and the setting to raise."""
	model = getattr(agent, "model", None)
	original = getattr(model, "ainvoke_stream", None) if model is not None else None
	if original is None:
		return

	async def stream_with_timeout_context(messages, *args, **kwargs):
		started = time.monotonic()
		chunk_count = 0
		try:
			async for chunk in original(messages, *args, **kwargs):
				chunk_count += 1
				yield chunk
		except Exception as exc:
			if _exception_is_stream_timeout(exc):
				raise _stream_timeout_error(model, time.monotonic() - started, chunk_count) from exc
			raise

	model.ainvoke_stream = stream_with_timeout_context


def _stream_timeout_error(model: Any, elapsed_seconds: float, chunk_count: int) -> Exception:
	from agno.exceptions import ModelProviderError

	return ModelProviderError(
		message=(
			f"Provider stream timed out after {elapsed_seconds:.0f}s while reading the model "
			f"response ({chunk_count} chunks received). The model was still generating "
			"a tool call. Increase AI Settings.stream_timeout if this persists."
		),
		status_code=504,
		model_name=getattr(model, "name", None),
		model_id=getattr(model, "id", None),
	)


def _frame_for_event(attempt: _TurnAttempt, event: Any) -> bytes | None:
	"""Update `attempt` from one Agno event and return the SSE frame to send, if there is one."""
	if isinstance(event, ModelRequestStartedEvent):
		attempt.model_call_index += 1
		attempt.model_call_started_at = time.monotonic()
	elif isinstance(event, ModelRequestCompletedEvent):
		_log_model_request(attempt, event)
	elif isinstance(event, RunOutput):
		attempt.final_output = event
	elif isinstance(event, RunErrorEvent):
		# The model call itself failed (bad credentials, provider outage, ...). On this path
		# Agno yields a RunErrorEvent and never a final RunOutput (a 401 from the provider gives
		# RunStartedEvent -> ModelRequestStartedEvent -> RunErrorEvent), so the failure cannot be
		# read from `final_output.status` the way a mid-run error can.
		attempt.run_error = event
	elif isinstance(event, RunContentEvent) and event.content:
		return _frame("text", {"content": event.content})
	elif isinstance(event, ToolCallStartedEvent) and event.tool:
		return _frame(
			"tool_started",
			{"id": event.tool.tool_call_id, "name": event.tool.tool_name, "arguments": event.tool.tool_args},
		)
	elif isinstance(event, ToolCallCompletedEvent) and event.tool:
		return _tool_ended_frame(attempt, event)
	return None


def _log_model_request(attempt: _TurnAttempt, event: ModelRequestCompletedEvent) -> None:
	started = attempt.model_call_started_at
	duration = f"{time.monotonic() - started:.2f}s" if started is not None else "n/a"
	attempt.call_log.append(
		f"call #{attempt.model_call_index} duration={duration} "
		f"input_tokens={event.input_tokens} output_tokens={event.output_tokens} "
		f"total_tokens={event.total_tokens} time_to_first_token={event.time_to_first_token}"
	)


def _tool_ended_frame(attempt: _TurnAttempt, event: ToolCallCompletedEvent) -> bytes | None:
	"""The `tool_ended` frame for a finished call, or None when the call was a confirmation pause.

	`event.content` is a short status string for the client display; the result fed back to the
	model is `event.tool.result`, which is what the diagnostic log measures."""
	tool = event.tool
	result_text = str(tool.result) if tool.result is not None else ""
	attempt.tool_log.append(
		f"tool={tool.tool_name} args={tool.tool_args} error={tool.tool_call_error} "
		f"result_chars={len(result_text)} result_preview={result_text[:200]!r}"
	)
	pending = _pending_from_tool_execution(tool) if tool.tool_call_error else None
	if pending:
		attempt.pending.append(pending)
		return None
	return _frame("tool_ended", {"id": tool.tool_call_id, "name": tool.tool_name, "result": event.content})


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
