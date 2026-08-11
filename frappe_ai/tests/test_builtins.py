# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for `frappe_ai/tools/builtins.py` — the ten builtin tools and
`sync_builtin_tools()`. Permission enforcement (`find_doctypes`/`describe`/`read`/
`create`/`update`/`delete`) is the security-relevant surface here, since these are
exactly the tools `frappe_ai.api.dispatch.dispatch_tool` calls after
`frappe.set_user(acting_user)` — this is the decisive test from
`001-architecture.md` §12: "a non-System-Manager user asking the agent to read a
DocType they lack permission on is refused by the tool."
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.tools.builtins import (
	BUILTIN_TOOLS,
	bind_search_knowledge,
	bind_update_memory,
	create,
	delete,
	describe,
	find_doctypes,
	read,
	sync_builtin_tools,
	update,
)

TEST_USER = "test-frappe-ai-builtins@example.com"


class TestSyncBuiltinTools(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_all_ten_as_system_generated(self):
		sync_builtin_tools()
		for builtin in BUILTIN_TOOLS:
			with self.subTest(tool=builtin.name):
				doc = frappe.get_doc("AI Tool", builtin.name)
				self.assertEqual(doc.type, "Imported")
				self.assertTrue(doc.is_system_generated)
				self.assertEqual(bool(doc.requires_confirmation), builtin.requires_confirmation)

	def test_resync_updates_existing_row_via_db_set_value(self):
		sync_builtin_tools()
		# db.set_value bypasses validate()'s immutability guard on purpose — confirm a
		# second sync doesn't raise even though these rows are is_system_generated.
		sync_builtin_tools()
		doc = frappe.get_doc("AI Tool", "read")
		self.assertTrue(doc.is_system_generated)


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


class TestKnowledgeAndMemoryFailClosed(IntegrationTestCase):
	"""`search_knowledge` and `update_memory` are both bound tools by this point."""

	def setUp(self):
		sync_builtin_tools()
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
					"tools": [{"tool": "update_memory"}],
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
