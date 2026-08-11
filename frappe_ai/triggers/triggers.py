# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import requests

import frappe
from frappe import _
from croniter import CroniterBadCronError, croniter

from frappe_ai.api.api import _check_agent_usable
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import create_run
from frappe_ai.service.auth import mint_run_token
from frappe_ai.utils.conditions import evaluate_condition

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

		prompt = frappe.render_template(
			trigger_doc.prompt_template,
			{"doc": doc, "now": frappe.utils.now_datetime()},
		)
		agent_doc = frappe.get_doc("AI Agent", trigger_doc.agent)
		_check_agent_usable(agent_doc, None)

		session_doc = frappe.get_doc(
			{
				"doctype": "AI Session",
				"agent": agent_doc.name,
				"source": "Trigger",
				"title": frappe.utils.cstr(target_name or trigger_doc.title)[:80],
			}
		).insert(ignore_permissions=True)
		run = create_run(
			source="Trigger",
			input=prompt,
			session=session_doc.name,
			trigger=trigger_doc.name,
			reference_doctype=target_doctype if doc else None,
			reference_name=target_name if doc else None,
			config_snapshot={**agent_doc._snapshot(), "auto_approve": bool(trigger_doc.auto_approve)},
		)
		session_doc.persist_turn(prompt, agent_doc.instructions, [], run.name)
		_run_via_service(run.name, session_doc.name, frappe.session.user)
		return run.name
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


def _run_via_service(run: str, session: str, user: str) -> None:
	"""Start the FastAPI run and consume its SSE stream until terminal state."""
	secret = frappe.conf.get("frappe_ai_service_secret")
	if not secret:
		frappe.throw(_("frappe_ai_service_secret is not set in site_config.json."), title=_("Service Not Configured"))
	service_base_url = frappe.get_cached_value("AI Settings", "AI Settings", "service_base_url")
	stream_timeout = frappe.get_cached_value("AI Settings", "AI Settings", "stream_timeout") or 600
	token = mint_run_token(run=run, session=session, user=user, secret=secret)

	response = requests.post(
		f"{(service_base_url or '').rstrip('/')}/stream/{run}",
		headers={"Authorization": f"Bearer {token}"},
		json={},
		stream=True,
		timeout=stream_timeout,
	)
	response.raise_for_status()
	last_event = ""
	for line in response.iter_lines(decode_unicode=True):
		if not line:
			continue
		if line.startswith("event: "):
			last_event = line.removeprefix("event: ").strip()
			continue
		if line.startswith("data: ") and last_event in {"done", "error"}:
			break

