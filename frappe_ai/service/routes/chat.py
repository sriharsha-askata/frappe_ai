# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Orchestrate streamed chat turns, confirmation, retry, and persistence."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from agno.agent import Agent
from agno.run.agent import RunStatus

from frappe_ai.lib.model import normalize_provider_error
from frappe_ai.service.builder import AgentBuilder, AgentBuildError
from frappe_ai.service.frappe_client import FrappeClient, FrappeClientError
from frappe_ai.service.routes.agno_events import (
	_done_payload,
	_error_payload,
	_frame,
	_TurnAttempt,
	stream_agent_turn,
)
from frappe_ai.service.routes.confirmation import (
	_build_result,
	_denied_result,
	_dispatch_approved,
	_redirects,
	_split_answers,
)
from frappe_ai.service.routes.transcript import _to_agno_messages

logger = logging.getLogger("frappe_ai.service.chat")


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
		attempt = _TurnAttempt()
		async with aclosing(stream_agent_turn(agent, messages, run, frappe_client, attempt)) as frames:
			async for frame in frames:
				yield frame

		# Retry once on the system default model only if the configured model failed before the
		# client received anything. Once a frame has gone out (text, or a tool starting or
		# finishing) a retry would repeat it and could re-run a tool's side effects, so the run
		# fails instead. Nothing is sent before the first model output, so a model that fails on
		# its first call (bad credentials, an outage) can still be retried.
		if attempt.run_error is not None and not attempt.pending and not attempt.output_sent:
			fallback_model_cfg = await _fallback_model_config(frappe_client, user)
			if fallback_model_cfg and fallback_model_cfg.get("model_id") != config["model"].get("model_id"):
				await frappe_client.log_diagnostic(
					f"frappe_ai Run {run}: retrying with fallback model",
					f"Primary model {config['model'].get('model_id')} failed with "
					f"{type(attempt.run_error).__name__}: {attempt.run_error!r}. "
					f"Retrying with fallback model {fallback_model_cfg.get('model_id')}.",
				)
				fallback_agent = _fallback_agent(agent, builder._build_model(fallback_model_cfg))
				attempt = _TurnAttempt()
				async with aclosing(
					stream_agent_turn(fallback_agent, messages, run, frappe_client, attempt)
				) as frames:
					async for frame in frames:
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


async def _fallback_model_config(frappe_client: FrappeClient, user: str) -> dict[str, Any] | None:
	"""The system default model to retry with, or None when it cannot be fetched."""
	try:
		return await frappe_client.get_fallback_model_config(user)
	except FrappeClientError:
		return None


def _fallback_agent(agent: Agent, model: Any) -> Agent:
	"""A copy of `agent` that runs on `model`, keeping its tools and presentation settings."""
	return Agent(
		model=model,
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
