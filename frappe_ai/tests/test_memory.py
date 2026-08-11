# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.memory.memory import build_memory_block, save_memory
from frappe_ai.tools.builtins import sync_builtin_tools


def _memory_agent(title: str = "Memory Test Agent") -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "Memory Test Model"):
		frappe.get_doc(
			{"doctype": "AI Model", "title": "Memory Test Model", "provider": "openai", "model_id": "gpt-4o-mini"}
		).insert(ignore_permissions=True)
	sync_builtin_tools()
	if not frappe.db.exists("AI Agent", title):
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": title,
				"model": "Memory Test Model",
				"instructions": "Remember facts.",
				"tools": [{"tool": "update_memory"}],
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
		block = build_memory_block(agent, query="What is the plant code?")
		self.assertIn("<agent_memory>", block)
		self.assertIn("PX-14", block)

