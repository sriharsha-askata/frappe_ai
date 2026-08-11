# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.tools.builtins import sync_builtin_tools
from frappe_ai.triggers import dispatch, dispatch_scheduled, fire


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

		with patch("frappe_ai.triggers.triggers._run_via_service"):
			run_name = fire(trig.name, target_doctype="ToDo", target_name=doc.name)

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

