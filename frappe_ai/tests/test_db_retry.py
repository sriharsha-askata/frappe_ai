# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Retrying a request after a transaction conflict (Postgres serialization failure, deadlock)."""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.utils.db_retry import is_conflict, retry_on_conflict


class _Conflict(Exception):
	pgcode = "40001"


class TestDbRetry(IntegrationTestCase):
	def test_serialization_failure_and_deadlock_are_conflicts(self):
		self.assertTrue(is_conflict(_Conflict()))
		self.assertTrue(is_conflict(frappe.QueryDeadlockError()))
		deadlock = _Conflict()
		deadlock.pgcode = "40P01"
		self.assertTrue(is_conflict(deadlock))

	def test_a_wrapped_conflict_is_found(self):
		try:
			try:
				raise _Conflict()
			except _Conflict as inner:
				raise RuntimeError("wrapped") from inner
		except RuntimeError as outer:
			self.assertTrue(is_conflict(outer))

	def test_other_errors_are_not_conflicts(self):
		self.assertFalse(is_conflict(ValueError("nope")))
		self.assertFalse(is_conflict(None))

	def test_a_conflict_is_retried_after_a_rollback(self):
		calls = []

		@retry_on_conflict
		def work(value):
			calls.append(value)
			if len(calls) < 3:
				raise _Conflict()
			return value * 2

		with patch("frappe.db.rollback") as rollback, patch("time.sleep"):
			self.assertEqual(work(21), 42)
		self.assertEqual(len(calls), 3)
		self.assertEqual(rollback.call_count, 2)

	def test_a_non_conflict_is_raised_immediately(self):
		calls = []

		@retry_on_conflict
		def work():
			calls.append(1)
			raise ValueError("real error")

		with patch("frappe.db.rollback") as rollback:
			with self.assertRaises(ValueError):
				work()
		self.assertEqual(len(calls), 1)
		rollback.assert_not_called()

	def test_it_gives_up_after_the_attempt_limit(self):
		calls = []

		@retry_on_conflict(attempts=3)
		def work():
			calls.append(1)
			raise _Conflict()

		with patch("frappe.db.rollback"), patch("time.sleep"):
			with self.assertRaises(_Conflict):
				work()
		self.assertEqual(len(calls), 3)

	def test_the_wrapper_keeps_the_function_signature(self):
		import inspect

		@retry_on_conflict
		def work(run: str, call_id: str | None = None):
			return run

		self.assertEqual(list(inspect.signature(work).parameters), ["run", "call_id"])
