# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Permission and behavior tests for the functions retained by native FAC tools."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from unittest.mock import patch

from frappe_ai.assistant_tools.native import ExecuteTool, RunActionTool
from frappe_ai.tools.builtins import (
	bind_search_knowledge,
	bind_update_memory,
	create,
	delete,
	describe,
	find_doctypes,
	read,
	update,
)

TEST_USER = "test-frappe-ai-builtins@example.com"


class TestFindDoctypesPermission(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_excludes_child_tables(self):
		results = find_doctypes(search="AI Session Message")
		self.assertEqual(results, [])

	def test_administrator_sees_results(self):
		results = find_doctypes(search="AI Agent")
		names = {r["name"] for r in results}
		self.assertIn("AI Agent", names)


class TestReadPermission(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_read_respects_limit_cap(self):
		results = read(doctype="DocType", limit=100000)
		self.assertLessEqual(len(results), 200)

	def test_guest_cannot_read_restricted_doctype(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			read(doctype="AI Provider")


class TestDescribePermission(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_describe_includes_permission_flags(self):
		result = describe(doctype="AI Agent")
		self.assertIn("permissions", result)
		self.assertIn("read", result["permissions"])

	def test_guest_describe_restricted_doctype_denied(self):
		frappe.set_user("Guest")
		with self.assertRaises(PermissionError):
			describe(doctype="AI Provider")


class TestCreateUpdateDeletePermission(IntegrationTestCase):
	"""Uses `ToDo` (a standard Frappe doctype, no custom validation) rather than `AI
	Provider`, whose `provider` field only accepts a fixed Agno slug set — irrelevant
	noise for tests about generic create/update/delete permission enforcement."""

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_create_denied_without_permission(self):
		frappe.set_user("Guest")
		with self.assertRaises(PermissionError):
			create(doctype="ToDo", records=[{"description": "not allowed"}])

	def test_create_partial_success_reports_failures(self):
		result = create(
			doctype="ToDo",
			records=[{"description": "ok"}, {"description": "bad", "assigned_by": "not-a-real-user@example.com"}],
		)
		self.assertEqual(len(result["created"]), 1)
		self.assertEqual(len(result.get("failures", [])), 1)

	def test_update_denied_without_permission(self):
		doc = frappe.get_doc({"doctype": "ToDo", "description": "update target"}).insert(ignore_permissions=True)
		frappe.set_user("Guest")
		result = update(doctype="ToDo", names=[doc.name], values={"status": "Closed"})
		self.assertEqual(result["updated"], [])
		self.assertEqual(len(result["failures"]), 1)

	def test_delete_denied_without_permission(self):
		doc = frappe.get_doc({"doctype": "ToDo", "description": "delete target"}).insert(ignore_permissions=True)
		frappe.set_user("Guest")
		result = delete(doctype="ToDo", names=[doc.name])
		self.assertEqual(result["deleted"], [])
		self.assertEqual(len(result["failures"]), 1)


class TestExecuteSandbox(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_execute_runs_and_returns_result(self):
		from frappe_ai.tools.builtins import execute

		result = execute(code="result = 1 + 1", description="add numbers")
		self.assertEqual(result, 2)

	def test_fac_execute_keeps_hardened_sandbox(self):
		with self.assertRaises(AttributeError):
			ExecuteTool().execute({"code": 'result = frappe.db.sql("select 1")', "description": "Run SQL"})


class TestFACRunActionParity(IntegrationTestCase):
	def test_fac_wrapper_uses_existing_action_implementation(self):
		with patch("frappe_ai.tools.builtins._apply_action", side_effect=["ok", ValueError("denied")]) as apply:
			result = RunActionTool().execute({"doctype": "ToDo", "names": ["A", "B"], "action": "submit"})
		self.assertEqual(result["results"], [{"name": "A", "result": "ok"}])
		self.assertEqual(result["failures"], [{"name": "B", "error": "denied"}])
		self.assertEqual(apply.call_count, 2)

	def test_fac_wrapper_preserves_rename_contract(self):
		with patch("frappe.rename_doc", return_value="New") as rename:
			result = RunActionTool().execute({"doctype": "ToDo", "names": ["Old"], "action": "rename", "args": {"new_name": "New"}})
		self.assertEqual(result, {"action": "rename", "old": "Old", "new": "New"})
		rename.assert_called_once_with("ToDo", "Old", "New")


class TestKnowledgeAndMemoryFailClosed(IntegrationTestCase):
	"""`search_knowledge` and `update_memory` are both bound tools by this point."""

	def setUp(self):
		if not frappe.db.exists("AI Provider", "openai"):
			frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
		if not frappe.db.exists("AI Model", "Builtins Memory Model"):
			frappe.get_doc(
				{
					"doctype": "AI Model",
					"title": "Builtins Memory Model",
					"provider": "openai",
					"model_id": "gpt-4o-mini",
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("AI Agent", "Builtins Memory Agent"):
			frappe.get_doc(
				{
					"doctype": "AI Agent",
					"title": "Builtins Memory Agent",
					"model": "Builtins Memory Model",
					"instructions": "Remember useful facts.",
				}
			).insert(ignore_permissions=True)

	def test_search_knowledge_unknown_kb_returns_empty(self):
		bound = bind_search_knowledge(["some-nonexistent-kb"])
		self.assertEqual(bound(query="anything"), [])

	def test_update_memory_adds_agent_memory(self):
		bound = bind_update_memory("Builtins Memory Agent")
		result = bound(content="Customer ACME prefers CSV exports.", scope="agent")
		self.assertEqual(result["action"], "added")
		doc = frappe.get_doc("AI Agent Memory", result["memory_id"])
		self.assertEqual(doc.agent, "Builtins Memory Agent")
		self.assertEqual(doc.scope, "Agent")
		self.assertEqual(doc.content, "Customer ACME prefers CSV exports.")

	def test_update_memory_rejects_unbound_agent(self):
		bound = bind_update_memory(None)
		with self.assertRaisesRegex(frappe.ValidationError, "memory enabled"):
			bound(content="a fact", scope="agent")
