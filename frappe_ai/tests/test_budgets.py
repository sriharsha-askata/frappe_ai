# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from frappe_ai.api.budgets import BudgetExceeded, consume
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import create_run
from frappe_ai.tests.test_api import _model_and_agent


class TestRuntimeBudget(IntegrationTestCase):
	def setUp(self):
		agent = _model_and_agent("Budget Agent")
		session = frappe.get_doc(
			{"doctype": "AI Session", "agent": agent, "source": "Manual", "title": "Budget Session"}
		).insert(ignore_permissions=True)
		self.run = create_run(
			source="Manual",
			input="x",
			session=session.name,
			config_snapshot={"max_runtime_seconds": 60},
		).name

	def tearDown(self):
		frappe.db.rollback()

	def test_new_run_has_a_segment_start(self):
		self.assertTrue(frappe.db.get_value("AI Run", self.run, "segment_started_at"))

	def test_run_within_limit_passes(self):
		consume(self.run)

	def test_old_creation_does_not_count_after_a_resume(self):
		frappe.db.set_value(
			"AI Run",
			self.run,
			{"creation": add_to_date(now_datetime(), hours=-1), "segment_started_at": now_datetime()},
			update_modified=False,
		)

		consume(self.run)

	def test_segment_older_than_limit_is_rejected(self):
		frappe.db.set_value(
			"AI Run", self.run, "segment_started_at", add_to_date(now_datetime(), minutes=-5), update_modified=False
		)

		with self.assertRaises(BudgetExceeded):
			consume(self.run)

	def test_missing_segment_start_falls_back_to_creation(self):
		frappe.db.set_value(
			"AI Run",
			self.run,
			{"segment_started_at": None, "creation": add_to_date(now_datetime(), minutes=-5)},
			update_modified=False,
		)

		with self.assertRaises(BudgetExceeded):
			consume(self.run)

	def test_counters_accumulate_across_calls(self):
		consume(self.run)
		consume(self.run, mutation=True)

		usage = json.loads(frappe.db.get_value("AI Run", self.run, "budget_usage"))
		self.assertEqual(usage["tool_calls"], 2)
		self.assertEqual(usage["mutations"], 1)
