# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for `AI Session` and `AI Run` — the owner chokepoints, the accumulating
`apply_result`, and prompt-message assembly. These are the properties `flow`'s audit
trail depended on, ported to `frappe_ai`'s split process model (see `AI Run`'s and
`AI Session`'s module docstrings for what's deliberately different).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.frappe_ai.doctype.ai_run.ai_run import assert_run_owner, create_run
from frappe_ai.frappe_ai.doctype.ai_session.ai_session import assert_session_owner, derive_title
from frappe_ai.knowledge.embedder import EmbeddingServiceUnavailable


def _model_and_agent(title: str = "Session Test Agent") -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "Session Test Model"):
		frappe.get_doc(
			{"doctype": "AI Model", "title": "Session Test Model", "provider": "openai", "model_id": "gpt-4o-mini"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Agent", title):
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": title,
				"model": "Session Test Model",
				"instructions": "You are helpful.",
				"tools": [{"tool": "read"}] if frappe.db.exists("AI Tool", "read") else [],
			}
		).insert(ignore_permissions=True)
	return title


def _session(**overrides: Any) -> dict:
	doc = {"doctype": "AI Session", "agent": _model_and_agent(), "source": "Manual"}
	doc.update(overrides)
	return doc


class TestDeriveTitle(IntegrationTestCase):
	def test_short_text_unchanged(self):
		self.assertEqual(derive_title("hello world"), "hello world")

	def test_collapses_whitespace(self):
		self.assertEqual(derive_title("hello   \n  world"), "hello world")

	def test_truncates_long_text(self):
		text = "x" * 200
		title = derive_title(text)
		self.assertEqual(len(title), 80)
		self.assertTrue(title.endswith("…"))


class TestAISessionAgentLocking(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_agent_locked_after_creation(self):
		doc = frappe.get_doc(_session()).insert(ignore_permissions=True)
		other_agent = _model_and_agent(title="Other Agent")
		doc.agent = other_agent
		with self.assertRaisesRegex(frappe.ValidationError, "agent"):
			doc.save(ignore_permissions=True)

	def test_model_override_allowed(self):
		doc = frappe.get_doc(_session()).insert(ignore_permissions=True)
		doc.model = "Session Test Model"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.model, "Session Test Model")


class TestAISessionPromptMessages(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_build_prompt_messages_injects_memory_block(self):
		sync_builtin_tools = frappe.get_attr("frappe_ai.tools.builtins.sync_builtin_tools")
		sync_builtin_tools()
		agent = _model_and_agent(title="Session Memory Agent")
		doc = frappe.get_doc("AI Agent", agent)
		if not any(row.tool == "update_memory" for row in doc.tools):
			doc.append("tools", {"tool": "update_memory"})
			doc.save(ignore_permissions=True)
		session = frappe.get_doc(_session(agent=agent)).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="Remember vendor preferences", session=session.name)
		session.persist_turn("What should I remember?", "You are helpful.", [], run.name)
		frappe.get_doc(
			{
				"doctype": "AI Agent Memory",
				"agent": agent,
				"scope": "Agent",
				"content": "Vendor Nova prefers invoices on Fridays.",
				"status": "Active",
			}
		).insert(ignore_permissions=True)

		messages = session.build_prompt_messages()
		self.assertEqual(messages[0]["role"], "system")
		self.assertIn("<agent_memory>", messages[0]["content"])
		self.assertIn("Vendor Nova prefers invoices on Fridays.", messages[0]["content"])

	def test_attachment_retrieval_falls_back_to_inline_when_embedding_unavailable(self):
		file_doc = frappe.get_doc(
			{"doctype": "File", "file_name": "large.txt", "content": "important attachment detail"}
		).insert(ignore_permissions=True)
		session = frappe.get_doc({"doctype": "AI Session", "source": "Manual"}).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="Find the attachment detail", session=session.name)
		with patch.object(session, "_index_retrieval_attachments"):
			with patch.object(session, "_attachment_inline_threshold", return_value=10):
				session.persist_turn(
					"Find the attachment detail",
					None,
					[
						{
							"file": file_doc.name,
							"file_name": "large.txt",
							"file_size": 100,
							"extracted_text": "important attachment detail",
						}
					],
					run.name,
				)

		with patch(
			"frappe_ai.knowledge.retriever.retrieve_attachments",
			side_effect=EmbeddingServiceUnavailable("Ollama is unavailable"),
		):
			messages = session.build_prompt_messages()

		self.assertIn("important", messages[-1]["content"])
		self.assertEqual(session.attachments[0].mode, "Inline")

	def test_oversized_attachment_index_failure_demotes_to_inline(self):
		file_doc = frappe.get_doc(
			{"doctype": "File", "file_name": "unavailable.txt", "content": "attachment content"}
		).insert(ignore_permissions=True)
		session = frappe.get_doc({"doctype": "AI Session", "source": "Manual"}).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="Read the attachment", session=session.name)
		with (
			patch.object(session, "_attachment_inline_threshold", return_value=10),
			patch("frappe_ai.knowledge.embedder._call_openai_compatible", side_effect=ConnectionError("refused")),
		):
			session.persist_turn(
				"Read the attachment",
				None,
				[
					{
						"file": file_doc.name,
						"file_name": "unavailable.txt",
						"file_size": 100,
						"extracted_text": "attachment content",
					}
				],
				run.name,
			)

		session.reload()
		self.assertEqual(session.attachments[0].mode, "Inline")


class TestAISessionModelEnabledValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_disabled_model_rejected(self):
		frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "Disabled Session Model",
				"provider": "openai" if frappe.db.exists("AI Provider", "openai") else None,
				"model_id": "gpt-4o-mini",
				"enabled": 0,
			}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(_session(model="Disabled Session Model"))
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			doc.insert(ignore_permissions=True)


class TestAssertSessionOwner(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_owner_passes(self):
		doc = frappe.get_doc(_session()).insert(ignore_permissions=True)
		# Administrator (the test runner's session user) is the owner by default.
		assert_session_owner(doc)  # should not raise

	def test_non_owner_without_write_permission_rejected(self):
		doc = frappe.get_doc(_session()).insert(ignore_permissions=True)
		frappe.db.set_value("AI Session", doc.name, "owner", "test-someone-else@example.com")

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			assert_session_owner(doc.name)


class TestAppendRunMessages(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_append_run_messages(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hello", session=session.name)

		session.append_run_messages(
			[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi there"}], run=run.name
		)
		session.reload()
		self.assertEqual(len(session.messages), 2)
		self.assertEqual(session.messages[0].role, "user")
		self.assertEqual(session.messages[1].content, "hi there")
		self.assertEqual(session.messages[0].run, run.name)


class TestAssertRunOwner(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_owner_passes(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hi", session=session.name)
		assert_run_owner(run)  # should not raise

	def test_non_owner_rejected(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hi", session=session.name)
		frappe.db.set_value("AI Run", run.name, "owner", "test-someone-else@example.com")

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			assert_run_owner(run.name)


class TestAIRunApplyResult(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_apply_result_completed(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hi", session=session.name)

		run.apply_result(
			{
				"status": "Completed",
				"iterations": 2,
				"output": "final answer",
				"tool_calls": [{"id": "1", "name": "read", "arguments": {}}],
				"usage": {"prompt_tokens": 10, "completion_tokens": 5},
				"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "final answer"}],
			}
		)

		self.assertEqual(run.status, "Completed")
		self.assertEqual(run.iterations, 2)
		self.assertEqual(run.output, "final answer")

	def test_apply_result_accumulates_iterations_and_usage_on_resume(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hi", session=session.name)

		run.apply_result(
			{
				"status": "Paused",
				"iterations": 1,
				"output": None,
				"tool_calls": [],
				"questions": [{"key": "call-1", "name": "create", "arguments": {}}],
				"usage": {"prompt_tokens": 10, "completion_tokens": 5},
				"messages": [{"role": "user", "content": "hi"}],
			}
		)
		self.assertEqual(run.status, "Paused")
		self.assertEqual(run.iterations, 1)

		# Resume: a second segment's iterations/usage must ADD to the first, not overwrite.
		run.apply_result(
			{
				"status": "Completed",
				"iterations": 1,
				"output": "done",
				"tool_calls": [],
				"usage": {"prompt_tokens": 3, "completion_tokens": 2},
				"messages": [
					{"role": "user", "content": "hi"},
					{"role": "assistant", "content": "done"},
				],
			}
		)
		self.assertEqual(run.status, "Completed")
		self.assertEqual(run.iterations, 2)

		import json

		usage = json.loads(run.usage)
		self.assertEqual(usage["prompt_tokens"], 13)
		self.assertEqual(usage["completion_tokens"], 7)

	def test_apply_result_appends_only_delta_messages(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hi", session=session.name)

		run.apply_result(
			{
				"status": "Completed",
				"iterations": 1,
				"output": "first",
				"tool_calls": [],
				"usage": {},
				"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "first"}],
			}
		)
		session.reload()
		self.assertEqual(len(session.messages), 2)

		# A second run on the same session: full transcript includes the prior two
		# messages plus new ones — only the new ones should be appended.
		run2 = create_run(source="Manual", input="again", session=session.name)
		run2.apply_result(
			{
				"status": "Completed",
				"iterations": 1,
				"output": "second",
				"tool_calls": [],
				"usage": {},
				"messages": [
					{"role": "user", "content": "hi"},
					{"role": "assistant", "content": "first"},
					{"role": "user", "content": "again"},
					{"role": "assistant", "content": "second"},
				],
			}
		)
		session.reload()
		self.assertEqual(len(session.messages), 4)
		self.assertEqual(session.messages[-1].content, "second")


class TestAIRunInvariants(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_paused_requires_questions(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{"doctype": "AI Run", "source": "Manual", "session": session.name, "status": "Paused"}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_failed_requires_error(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{"doctype": "AI Run", "source": "Manual", "session": session.name, "status": "Failed"}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_mark_failed_truncates_error(self):
		session = frappe.get_doc(_session()).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="hi", session=session.name)
		run.mark_failed("x" * 6000)
		self.assertEqual(run.status, "Failed")
		self.assertEqual(len(run.error), 5000)
