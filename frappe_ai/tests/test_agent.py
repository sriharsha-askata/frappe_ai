# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for `AI Agent` (`frappe_ai/frappe_ai/doctype/ai_agent/ai_agent.py`)."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.tests import IntegrationTestCase


def _model_and_provider() -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "Test Agent Model"):
		frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "Test Agent Model",
				"provider": "openai",
				"model_id": "gpt-4o-mini",
			}
		).insert(ignore_permissions=True)
	return "Test Agent Model"


def _agent(**overrides: Any) -> dict:
	doc = {
		"doctype": "AI Agent",
		"title": overrides.pop("title", "Test Agent"),
		"model": _model_and_provider(),
		"instructions": "You are a helpful assistant.",
	}
	doc.update(overrides)
	return doc


class TestAIAgentDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_default_tools_seeded_when_available(self):
		# Builtins are synced via after_migrate in a real install; ensure at least
		# the ones this test depends on exist so before_insert has something to seed.
		from frappe_ai.tools.builtins import sync_builtin_tools

		sync_builtin_tools()
		doc = frappe.get_doc(_agent(title="Default Tools Agent")).insert()
		slugs = {row.tool for row in doc.tools}
		self.assertEqual(slugs, {"describe", "read", "execute"})

	def test_explicit_tools_not_overridden(self):
		from frappe_ai.tools.builtins import sync_builtin_tools

		sync_builtin_tools()
		doc = frappe.get_doc(_agent(title="Explicit Tools Agent", tools=[{"tool": "read"}])).insert()
		self.assertEqual([row.tool for row in doc.tools], ["read"])


class TestAIAgentMaxIterations(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_default_max_iterations(self):
		doc = frappe.get_doc(_agent(title="Iter Agent")).insert()
		reloaded = frappe.get_doc("AI Agent", doc.name)
		self.assertEqual(reloaded.max_iterations, 10)

	def test_zero_max_iterations_rejected(self):
		doc = frappe.get_doc(_agent(title="Bad Iter Agent", max_iterations=0))
		with self.assertRaisesRegex(frappe.ValidationError, "Max Iterations"):
			doc.insert()


class TestAIAgentKnowledgeSearchTool(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_knowledge_base_binding_appends_search_tool(self):
		from frappe_ai.tools.builtins import sync_builtin_tools

		sync_builtin_tools()
		doc = frappe.get_doc(
			_agent(
				title="KB Agent",
				tools=[{"tool": "read"}],
				knowledge_bases=[{"knowledge_base": "Nonexistent KB"}],
			)
		)
		# AI Knowledge Base doesn't exist until Phase 4 — ignore_links lets this test
		# exercise _ensure_knowledge_search_tool's logic (a non-empty knowledge_bases
		# table) without a real linkable KB doc to point at.
		doc.insert(ignore_links=True)
		slugs = {row.tool for row in doc.tools}
		self.assertIn("search_knowledge", slugs)

	def test_no_knowledge_bases_no_search_tool_appended(self):
		doc = frappe.get_doc(_agent(title="No KB Agent", tools=[{"tool": "read"}])).insert()
		slugs = {row.tool for row in doc.tools}
		self.assertNotIn("search_knowledge", slugs)


class TestAIAgentSnapshot(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_snapshot_shape(self):
		doc = frappe.get_doc(_agent(title="Snapshot Agent", tools=[{"tool": "read"}])).insert()
		snapshot = doc._snapshot()
		self.assertEqual(snapshot["title"], "Snapshot Agent")
		self.assertEqual(snapshot["tools"], ["read"])
		self.assertEqual(snapshot["max_iterations"], 10)
		self.assertIn("temperature", snapshot)

	def test_snapshot_model_override(self):
		doc = frappe.get_doc(_agent(title="Override Agent")).insert()
		snapshot = doc._snapshot(model="Some Other Model")
		self.assertEqual(snapshot["model"], "Some Other Model")


class TestAIAgentImmutability(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_delete_blocked_for_system_generated(self):
		doc = frappe.get_doc(_agent(title="Sys Agent", is_system_generated=1)).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			doc.delete()
