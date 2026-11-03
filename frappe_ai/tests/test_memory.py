# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.memory.memory import (
	INJECT_ALL_CAP,
	RECENT_ALWAYS,
	SEARCH_TOP_K,
	_select_relevant,
	build_memory_block,
	save_memory,
)


def _memory_agent(title: str = "Memory Test Agent") -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "Memory Test Model"):
		frappe.get_doc(
			{"doctype": "AI Model", "title": "Memory Test Model", "provider": "openai", "model_id": "gpt-4o-mini"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Agent", title):
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": title,
				"model": "Memory Test Model",
				"instructions": "Remember facts.",
			}
		).insert(ignore_permissions=True)
	return title


class TestMemory(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_save_memory_adds_agent_memory(self):
		agent = _memory_agent()
		result = save_memory(agent, content="ACME closes books on Wednesday.", scope="agent")
		doc = frappe.get_doc("AI Agent Memory", result["memory_id"])
		self.assertEqual(doc.agent, agent)
		self.assertEqual(doc.scope, "Agent")

	def test_build_memory_block_includes_active_memory(self):
		agent = _memory_agent("Memory Block Agent")
		frappe.get_doc(
			{
				"doctype": "AI Agent Memory",
				"agent": agent,
				"scope": "Agent",
				"content": "Remember the plant code PX-14.",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		with patch("frappe_ai.memory.memory._has_memory_tool", return_value=True):
			block = build_memory_block(agent, query="What is the plant code?")
		self.assertIn("<agent_memory>", block)
		self.assertIn("PX-14", block)


def _rows(count: int):
	return [
		SimpleNamespace(name=f"MEM-{i:03d}", content=f"fact number {i}", keywords=None, modified=i)
		for i in range(count)
	]


class TestMemorySelection(IntegrationTestCase):
	"""Which memories are shown when there are more than can be listed."""

	def test_keyword_matches_are_kept_with_the_most_recent(self):
		rows = _rows(INJECT_ALL_CAP + 10)
		rows[2].content = "invoices are approved by the finance lead"
		chosen = {row.name for row in _select_relevant(rows, "who approves invoices? finance lead")}
		self.assertIn("MEM-002", chosen)
		newest = {row.name for row in sorted(rows, key=lambda r: r.modified, reverse=True)[:RECENT_ALWAYS]}
		self.assertTrue(newest <= chosen)

	def test_keywords_field_is_searched(self):
		rows = _rows(INJECT_ALL_CAP + 5)
		rows[1].keywords = "payroll"
		chosen = {row.name for row in _select_relevant(rows, "payroll")}
		self.assertIn("MEM-001", chosen)

	def test_no_match_falls_back_to_recency(self):
		rows = _rows(INJECT_ALL_CAP + 5)
		chosen = _select_relevant(rows, "zzz qqq")
		self.assertEqual(len(chosen), INJECT_ALL_CAP)
		self.assertEqual({row.name for row in chosen}, {row.name for row in rows[-INJECT_ALL_CAP:]})

	def test_blank_query_falls_back_to_recency(self):
		self.assertEqual(len(_select_relevant(_rows(INJECT_ALL_CAP + 5), "  ")), INJECT_ALL_CAP)

	def test_matches_are_capped(self):
		rows = _rows(INJECT_ALL_CAP + SEARCH_TOP_K + 20)
		for row in rows:
			row.content = "shared topic"
		chosen = _select_relevant(rows, "shared topic")
		self.assertLessEqual(len(chosen), SEARCH_TOP_K + RECENT_ALWAYS)
