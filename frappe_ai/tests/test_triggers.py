# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.tools.builtins import sync_builtin_tools
from frappe_ai.triggers import dispatch, dispatch_scheduled, fire, fire_manual_trigger
from frappe_ai.triggers.triggers import _run_via_service


def _trigger_agent(title: str = "Trigger Test Agent") -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "Trigger Test Model"):
		frappe.get_doc(
			{"doctype": "AI Model", "title": "Trigger Test Model", "provider": "openai", "model_id": "gpt-4o-mini"}
		).insert(ignore_permissions=True)
	sync_builtin_tools()
	if not frappe.db.exists("AI Agent", title):
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": title,
				"model": "Trigger Test Model",
				"instructions": "Be terse.",
				"tools": [{"tool": "read"}],
			}
		).insert(ignore_permissions=True)
	return title


def _trigger(agent_name: str, **overrides):
	doc = {
		"doctype": "AI Trigger",
		"title": "Trigger Test",
		"agent": agent_name,
		"enabled": 1,
		"event": "DocType Event",
		"target_doctype": "ToDo",
		"doc_event": "after_insert",
		"prompt_template": "New {{ doc.doctype }} {{ doc.name }}",
	}
	doc.update(overrides)
	return doc


class TestTriggers(IntegrationTestCase):
	def setUp(self):
		frappe.reload_doc("frappe_ai", "doctype", "ai_trigger")

	def tearDown(self):
		frappe.db.rollback()

	def test_dispatch_enqueues_matching_trigger(self):
		agent = _trigger_agent()
		trig = frappe.get_doc(_trigger(agent)).insert(ignore_permissions=True)
		doc = frappe.get_doc({"doctype": "ToDo", "description": "dispatch"}).insert(ignore_permissions=True)

		with patch("frappe.enqueue") as enqueue:
			dispatch(doc, "after_insert")

		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["trigger"], trig.name)

	def test_fire_creates_trigger_run(self):
		agent = _trigger_agent("Trigger Fire Agent")
		trig = frappe.get_doc(_trigger(agent)).insert(ignore_permissions=True)
		doc = frappe.get_doc({"doctype": "ToDo", "description": "fire"}).insert(ignore_permissions=True)

		with (
			patch("frappe_ai.triggers.triggers._run_via_service"),
			patch("frappe_ai.triggers.triggers.frappe.db.commit") as commit,
		):
			run_name = fire(trig.name, target_doctype="ToDo", target_name=doc.name)

		commit.assert_called_once()
		run = frappe.get_doc("AI Run", run_name)
		self.assertEqual(run.source, "Trigger")
		self.assertEqual(run.trigger, trig.name)
		self.assertEqual(run.reference_name, doc.name)

	def test_dispatch_scheduled_enqueues_due_trigger(self):
		agent = _trigger_agent("Trigger Scheduled Agent")
		trig = frappe.get_doc(
			_trigger(
				agent,
				event="Scheduled",
				target_doctype=None,
				doc_event=None,
				cron_expression="* * * * *",
				prompt_template="run",
			)
		).insert(ignore_permissions=True)
		past = frappe.utils.now_datetime() - timedelta(minutes=5)
		frappe.db.set_value("AI Trigger", trig.name, "last_fired_at", past, update_modified=False)

		with patch("frappe.enqueue") as enqueue:
			dispatch_scheduled()

		enqueue.assert_called_once()

	def test_manual_trigger_saves_without_event_fields(self):
		agent = _trigger_agent("Trigger Manual Validation Agent")

		doc = frappe.get_doc(
			_trigger(
				agent,
				event="Manual",
				target_doctype=None,
				doc_event=None,
				cron_expression=None,
				prompt_template="Manual {{ context.enquiry }} @ {{ now }}",
			)
		).insert(ignore_permissions=True)

		self.assertEqual(doc.event, "Manual")

	def test_fire_manual_trigger_creates_run_from_context(self):
		agent = _trigger_agent("Trigger Manual Fire Agent")
		doc = frappe.get_doc({"doctype": "ToDo", "description": "manual fire"}).insert(ignore_permissions=True)
		trig = frappe.get_doc(
			_trigger(
				agent,
				title="Manual Trigger Test",
				event="Manual",
				target_doctype=None,
				doc_event=None,
				cron_expression=None,
				prompt_template="Manual {{ context.enquiry }} {{ context.stage_log_id }}",
			)
		).insert(ignore_permissions=True)

		with (
			patch("frappe_ai.triggers.triggers._run_via_service"),
			patch("frappe_ai.triggers.triggers.frappe.db.commit") as commit,
		):
			result = fire_manual_trigger(
				trig.name,
				context={"enquiry": "ENQ-1", "stage_log_id": "SL-1"},
				reference_doctype="ToDo",
				reference_name=doc.name,
			)

		commit.assert_called_once()
		run = frappe.get_doc("AI Run", result["run"])
		self.assertEqual(run.source, "Trigger")
		self.assertEqual(run.trigger, trig.name)
		self.assertEqual(run.reference_name, doc.name)
		self.assertEqual(run.input, "Manual ENQ-1 SL-1")
		self.assertEqual(result["trigger"], trig.name)

	def test_run_via_service_error_event_requires_failed_run_state(self):
		class Response:
			def raise_for_status(self):
				return None

			def iter_lines(self, decode_unicode=True):
				yield "event: error"
				yield 'data: {"message": "provider failed"}'

		original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = "test-secret"
		try:
			with (
				patch("frappe_ai.triggers.triggers.mint_run_token", return_value="token"),
				patch("frappe_ai.triggers.triggers.requests.post", return_value=Response()),
				patch("frappe_ai.triggers.triggers.frappe.get_cached_value", return_value="http://service"),
				patch("frappe_ai.triggers.triggers.frappe.db.get_value", return_value="Running"),
				patch("frappe_ai.triggers.triggers.frappe.db.commit"),
				patch("frappe_ai.triggers.triggers.TERMINAL_STATUS_WAIT_SECONDS", 0),
				patch("frappe_ai.triggers.triggers.frappe.log_error"),
			):
				with self.assertRaisesRegex(RuntimeError, "provider failed"):
					_run_via_service("RUN-1", "SESSION-1", "test@example.com")
		finally:
			if original_secret is None:
				frappe.conf.pop("frappe_ai_service_secret", None)
			else:
				frappe.conf.frappe_ai_service_secret = original_secret

	def test_run_via_service_done_waits_for_terminal_db_state(self):
		class Response:
			def raise_for_status(self):
				return None

			def iter_lines(self, decode_unicode=True):
				yield "event: done"
				yield 'data: {"status": "Completed"}'

		original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = "test-secret"
		try:
			with (
				patch("frappe_ai.triggers.triggers.mint_run_token", return_value="token"),
				patch("frappe_ai.triggers.triggers.requests.post", return_value=Response()),
				patch("frappe_ai.triggers.triggers.frappe.get_cached_value", return_value="http://service"),
				patch("frappe_ai.triggers.triggers.frappe.db.get_value", side_effect=["Running", "Completed"]),
				patch("frappe_ai.triggers.triggers.frappe.db.commit") as commit,
				patch("frappe_ai.triggers.triggers.time.sleep"),
				patch("frappe_ai.triggers.triggers.frappe.log_error"),
			):
				_run_via_service("RUN-1", "SESSION-1", "test@example.com")
				self.assertEqual(commit.call_count, 2)
		finally:
			if original_secret is None:
				frappe.conf.pop("frappe_ai_service_secret", None)
			else:
				frappe.conf.frappe_ai_service_secret = original_secret
