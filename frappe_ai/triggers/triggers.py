# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import requests
from requests import RequestException

import frappe
from frappe import _
from croniter import CroniterBadCronError, croniter

from frappe_ai.api.api import _check_agent_usable
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import create_run
from frappe_ai.service.auth import mint_run_token
from frappe_ai.utils.conditions import evaluate_condition

TERMINAL_STATUS_WAIT_SECONDS = 2.0
TERMINAL_STATUS_POLL_SECONDS = 0.1

if TYPE_CHECKING:
	from frappe.model.document import Document

DOC_EVENTS = frozenset({"after_insert", "on_update", "on_submit", "on_cancel", "on_trash"})
AI_INTERNAL_DOCTYPES = frozenset(
	{
		"AI Provider",
		"AI Model",
		"AI Settings",
		"AI Agent",
		"AI Agent Tool",
		"AI Agent Tool Config",
		"AI Agent Knowledge Base",
		"AI Tool",
		"AI Session",
		"AI Session Message",
		"AI Session Attachment",
		"AI Run",
		"AI Knowledge Base",
		"AI Knowledge Source",
		"AI Knowledge Chunk",
		"AI Trigger",
		"AI Agent Memory",
		"AI MCP Connection",
		"AI Agent MCP Connection",
	}
)


def dispatch(doc: "Document", method: str | None = None) -> None:
	"""doc_events hook: enqueue matching DocType-event triggers."""
	if method not in DOC_EVENTS:
		return
	if doc.doctype in AI_INTERNAL_DOCTYPES:
		return
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_install_db:
		return

	for trigger in _doctype_triggers(doc.doctype, method):
		if trigger.condition and not _passes_condition(trigger, doc):
			continue
		frappe.enqueue(
			"frappe_ai.triggers.fire",
			enqueue_after_commit=True,
			trigger=trigger.name,
			target_doctype=doc.doctype,
			target_name=doc.name,
		)


def dispatch_scheduled() -> None:
	"""Scheduler hook: fire any due scheduled triggers."""
	now = frappe.utils.now_datetime()
	triggers = frappe.get_all(
		"AI Trigger",
		filters={"event": "Scheduled", "enabled": 1},
		fields=["name", "cron_expression", "last_fired_at", "creation"],
	)
	for trigger in triggers:
		anchor = frappe.utils.get_datetime(trigger.last_fired_at or trigger.creation)
		try:
			next_run = croniter(trigger.cron_expression, anchor).get_next(datetime)
		except (CroniterBadCronError, ValueError):
			frappe.log_error(title=f"AI Trigger cron parse failed: {trigger.name}")
			continue
		if next_run <= now:
			frappe.db.set_value("AI Trigger", trigger.name, "last_fired_at", now, update_modified=False)
			frappe.enqueue("frappe_ai.triggers.fire", trigger=trigger.name)


def fire(trigger: str, target_doctype: str | None = None, target_name: str | None = None) -> str | None:
	"""Worker: render the prompt and run the agent through the FastAPI service."""
	trigger_doc = frappe.get_doc("AI Trigger", trigger)
	if not trigger_doc.enabled:
		return None
	if trigger_doc.event != "DocType Event":
		frappe.throw(_("AI Trigger {0} is not a DocType Event trigger.").format(trigger_doc.name))

	original_user = frappe.session.user
	frappe.set_user(trigger_doc.run_as or trigger_doc.owner)
	try:
		doc = None
		if target_doctype and target_name:
			try:
				doc = frappe.get_doc(target_doctype, target_name)
			except frappe.DoesNotExistError:
				return None
			if trigger_doc.condition and not _eval_condition(trigger_doc.condition, doc):
				return None

		result = _create_and_run_trigger(
			trigger_doc,
			prompt_context={"doc": doc, "now": frappe.utils.now_datetime()},
			session_source="Trigger",
			run_source="Trigger",
			session_title=frappe.utils.cstr(target_name or trigger_doc.title)[:80],
			reference_doctype=target_doctype if doc else None,
			reference_name=target_name if doc else None,
			acting_user=frappe.session.user,
		)
		return result["run"]
	finally:
		frappe.set_user(original_user)


def fire_manual_trigger(
	trigger: str,
	context: dict[str, Any] | None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	run_as: str | None = None,
	session_title: str | None = None,
) -> dict[str, str]:
	"""Run a Manual AI Trigger with app-supplied context."""
	frappe.log_error(
		title="frappe_ai Manual Trigger: called",
		message=frappe.as_json(
			{
				"trigger": trigger,
				"context": context,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"run_as": run_as,
				"session_user": frappe.session.user,
			}
		),
	)
	trigger_doc = frappe.get_doc("AI Trigger", trigger)
	if not trigger_doc.enabled:
		frappe.throw(_("AI Trigger {0} is disabled.").format(trigger_doc.name), title=_("Disabled Trigger"))
	if trigger_doc.event != "Manual":
		frappe.throw(_("AI Trigger {0} is not a Manual trigger.").format(trigger_doc.name), title=_("Invalid Trigger"))
	if context is not None and not isinstance(context, dict):
		frappe.throw(_("Manual trigger context must be a dictionary."), title=_("Invalid Context"))

	original_user = frappe.session.user
	acting_user = run_as or trigger_doc.run_as or trigger_doc.owner
	frappe.set_user(acting_user)
	try:
		result = _create_and_run_trigger(
			trigger_doc,
			prompt_context={"context": context or {}, "now": frappe.utils.now_datetime()},
			session_source="Trigger",
			run_source="Trigger",
			session_title=frappe.utils.cstr(session_title or reference_name or trigger_doc.title)[:80],
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			acting_user=frappe.session.user,
		)
		frappe.log_error(
			title="frappe_ai Manual Trigger: completed",
			message=frappe.as_json({**result, "context": context}),
		)
		return result
	finally:
		frappe.set_user(original_user)


def _doctype_triggers(target_doctype: str, doc_event: str) -> list[Any]:
	return frappe.get_all(
		"AI Trigger",
		filters={
			"event": "DocType Event",
			"target_doctype": target_doctype,
			"doc_event": doc_event,
			"enabled": 1,
		},
		fields=["name", "condition", "run_as", "owner"],
	)


def _passes_condition(trigger, doc: "Document") -> bool:
	original_user = frappe.session.user
	frappe.set_user(trigger.run_as or trigger.owner)
	try:
		return _eval_condition(trigger.condition, doc)
	finally:
		frappe.set_user(original_user)


def _eval_condition(condition: str, doc: "Document") -> bool:
	context = {"doc": doc, "utils": frappe.utils}
	try:
		return evaluate_condition(condition, context)
	except Exception:
		frappe.log_error(title="AI Trigger condition eval failed")
		return False


def _create_and_run_trigger(
	trigger_doc,
	*,
	prompt_context: dict[str, Any],
	session_source: str,
	run_source: str,
	session_title: str,
	reference_doctype: str | None,
	reference_name: str | None,
	acting_user: str,
) -> dict[str, str]:
	prompt = frappe.render_template(trigger_doc.prompt_template, prompt_context)
	agent_doc = frappe.get_doc("AI Agent", trigger_doc.agent)
	_check_agent_usable(agent_doc, None)
	frappe.log_error(
		title="frappe_ai Trigger Run: creating",
		message=frappe.as_json(
			{
				"trigger": trigger_doc.name,
				"agent": agent_doc.name,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"acting_user": acting_user,
				"context": prompt_context.get("context"),
			}
		),
	)

	session_doc = frappe.get_doc(
		{
			"doctype": "AI Session",
			"agent": agent_doc.name,
			"source": session_source,
			"title": session_title,
		}
	).insert(ignore_permissions=True)
	run = create_run(
		source=run_source,
		input=prompt,
		session=session_doc.name,
		trigger=trigger_doc.name,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		config_snapshot={**agent_doc._snapshot(), "auto_approve": bool(trigger_doc.auto_approve)},
	)
	session_doc.persist_turn(prompt, agent_doc.instructions, [], run.name)
	frappe.db.commit()
	frappe.log_error(
		title="frappe_ai Trigger Run: committed before service",
		message=frappe.as_json(
			{
				"trigger": trigger_doc.name,
				"run": run.name,
				"session": session_doc.name,
				"acting_user": acting_user,
				"context": prompt_context.get("context"),
			}
		),
	)
	frappe.log_error(
		title="frappe_ai Trigger Run: service starting",
		message=frappe.as_json(
			{
				"trigger": trigger_doc.name,
				"run": run.name,
				"session": session_doc.name,
				"acting_user": acting_user,
				"context": prompt_context.get("context"),
			}
		),
	)
	_run_via_service(run.name, session_doc.name, acting_user)
	run_status = frappe.db.get_value("AI Run", run.name, "status")
	frappe.log_error(
		title="frappe_ai Trigger Run: service finished",
		message=frappe.as_json(
			{
				"trigger": trigger_doc.name,
				"run": run.name,
				"session": session_doc.name,
				"status": run_status,
				"context": prompt_context.get("context"),
			}
		),
	)
	return {
		"run": run.name,
		"session": session_doc.name,
		"trigger": trigger_doc.name,
		"user": acting_user,
	}


def _run_via_service(run: str, session: str, user: str) -> None:
	"""Start the FastAPI run and consume its SSE stream until terminal state."""
	secret = frappe.conf.get("frappe_ai_service_secret")
	if not secret:
		frappe.throw(_("frappe_ai_service_secret is not set in site_config.json."), title=_("Service Not Configured"))
	service_base_url = frappe.get_cached_value("AI Settings", "AI Settings", "service_base_url")
	stream_timeout = frappe.get_cached_value("AI Settings", "AI Settings", "stream_timeout") or 600
	token = mint_run_token(run=run, session=session, user=user, secret=secret)
	frappe.log_error(
		title="frappe_ai Service Stream: POST",
		message=frappe.as_json(
			{
				"run": run,
				"session": session,
				"user": user,
				"service_base_url": service_base_url,
				"stream_timeout": stream_timeout,
			}
		),
	)

	try:
		response = requests.post(
			f"{(service_base_url or '').rstrip('/')}/stream/{run}",
			headers={"Authorization": f"Bearer {token}"},
			json={},
			stream=True,
			timeout=stream_timeout,
		)
		response.raise_for_status()
		last_event = ""
		saw_terminal_event = False
		terminal_event = ""
		terminal_payload = ""
		for line in response.iter_lines(decode_unicode=True):
			if not line:
				continue
			if line.startswith("event: "):
				last_event = line.removeprefix("event: ").strip()
				continue
			if line.startswith("data: ") and last_event in {"done", "error"}:
				saw_terminal_event = True
				terminal_event = last_event
				terminal_payload = line.removeprefix("data: ").strip()
				frappe.log_error(
					title="frappe_ai Service Stream: terminal event",
					message=frappe.as_json(
						{"run": run, "session": session, "event": last_event, "payload": terminal_payload}
					),
				)
				break
		if saw_terminal_event:
			status = _wait_for_terminal_run_status(run)
			if terminal_event == "done" and status in {"Completed", "Paused"}:
				return
			if terminal_event == "error" and status == "Failed":
				return
			frappe.log_error(
				title="frappe_ai Service Stream: terminal event without terminal db state",
				message=frappe.as_json(
					{
						"run": run,
						"session": session,
						"event": terminal_event,
						"status": status,
						"payload": terminal_payload,
					}
				),
			)
			error_message = _stream_error_message(terminal_payload) if terminal_event == "error" else None
			raise RuntimeError(
				error_message
				or _(
					"frappe_ai service emitted {0} but the run is still {1}."
				).format(terminal_event, status or _("missing"))
			)
		if not saw_terminal_event:
			status = frappe.db.get_value("AI Run", run, "status")
			if status in {"Completed", "Failed", "Paused"}:
				frappe.log_error(
					title="frappe_ai Service Stream: terminal db state",
					message=frappe.as_json({"run": run, "session": session, "status": status}),
				)
				return
			frappe.log_error(
				title="frappe_ai Service Stream: missing terminal event",
				message=frappe.as_json({"run": run, "session": session, "status": status}),
			)
			raise RuntimeError(
				_(
					"frappe_ai service stream ended before emitting a terminal event and before the run reached a terminal state."
				)
			)
	except RequestException as exc:
		status = frappe.db.get_value("AI Run", run, "status")
		if status in {"Completed", "Failed", "Paused"}:
			# The service can persist the terminal run state successfully and still lose the
			# outbound chunked response before bench sees the final SSE frame. In that case,
			# prefer the persisted run state over the transport error.
			frappe.log_error(
				title="frappe_ai Service Stream: transport error after terminal db state",
				message=frappe.as_json({"run": run, "session": session, "status": status, "error": str(exc)}),
			)
			return
		frappe.log_error(
			title="frappe_ai Service Stream: transport error",
			message=frappe.as_json({"run": run, "session": session, "status": status, "error": str(exc)}),
		)
		raise RuntimeError(_("frappe_ai service stream failed before the run reached a terminal state: {0}").format(exc))


def _stream_error_message(payload: str) -> str | None:
	if not payload:
		return None
	try:
		data = json.loads(payload)
	except ValueError:
		return payload
	return data.get("message") or data.get("error") or payload


def _wait_for_terminal_run_status(run: str) -> str | None:
	deadline = time.monotonic() + TERMINAL_STATUS_WAIT_SECONDS
	frappe.db.commit()
	status = frappe.db.get_value("AI Run", run, "status")
	while status not in {"Completed", "Failed", "Paused"} and time.monotonic() < deadline:
		time.sleep(TERMINAL_STATUS_POLL_SECONDS)
		frappe.db.commit()
		status = frappe.db.get_value("AI Run", run, "status")
	return status
