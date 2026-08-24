# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for `frappe_ai/api/api.py` (browser-facing whitelisted methods) and
`frappe_ai/api/dispatch.py` (the security-critical tool-dispatch endpoint, ADR 0003).

`dispatch_tool`'s `frappe.set_user(acting_user)` scoping is the decisive test here —
it's the mechanism `001-architecture.md` §12 names explicitly: "a non-System-Manager
user asking the agent to read a DocType they lack permission on is refused by the
tool." Everything else in `dispatch.py` is secondary to getting that right.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.api import api, dispatch, frontend
from frappe_ai.assistant_tools.native import SearchKnowledgeTool, UpdateMemoryTool
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import create_run
from frappe_ai.tools.builtins import sync_builtin_tools

TEST_SECRET = "test-service-secret-for-dispatch-tests"


def _patch_request_header(header_value: str | None):
	def _fake(key, default=None):
		if key == "X-Frappe-AI-Service-Secret":
			return header_value
		return default

	return _fake


def _model_and_agent(title: str = "API Test Agent") -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "API Test Model"):
		frappe.get_doc(
			{"doctype": "AI Model", "title": "API Test Model", "provider": "openai", "model_id": "gpt-4o-mini"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Agent", title):
		sync_builtin_tools()
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": title,
				"model": "API Test Model",
				"instructions": "You are helpful.",
				"tools": [{"tool": "read"}],
			}
		).insert(ignore_permissions=True)
	return title


class TestDispatchToolServiceSecretAuth(IntegrationTestCase):
	def setUp(self):
		self._original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = TEST_SECRET
		sync_builtin_tools()

	def tearDown(self):
		if self._original_secret is None:
			frappe.conf.pop("frappe_ai_service_secret", None)
		else:
			frappe.conf.frappe_ai_service_secret = self._original_secret
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_missing_secret_rejected(self):
		with patch("frappe.get_request_header", new=_patch_request_header(None)):
			with self.assertRaises(frappe.AuthenticationError):
				dispatch.dispatch_tool(tool="read", user="Administrator", arguments={"doctype": "DocType"})

	def test_wrong_secret_rejected(self):
		with patch("frappe.get_request_header", new=_patch_request_header("wrong")):
			with self.assertRaises(frappe.AuthenticationError):
				dispatch.dispatch_tool(tool="read", user="Administrator", arguments={"doctype": "DocType"})


class TestDispatchToolActingUserScoping(IntegrationTestCase):
	"""The decisive test (001-architecture.md §12): dispatch must run permission
	checks as the acting `user` argument, not as whatever identity would otherwise
	be implied by the service secret."""

	def setUp(self):
		self._original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = TEST_SECRET
		sync_builtin_tools()
		if not frappe.db.exists("User", "test-dispatch-guest@example.com"):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": "test-dispatch-guest@example.com",
					"first_name": "Dispatch Guest",
					"send_welcome_email": 0,
					"roles": [],
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		if self._original_secret is None:
			frappe.conf.pop("frappe_ai_service_secret", None)
		else:
			frappe.conf.frappe_ai_service_secret = self._original_secret
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_unprivileged_user_refused_by_tool(self):
		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = dispatch.dispatch_tool(
				tool="read",
				user="test-dispatch-guest@example.com",
				arguments={"doctype": "AI Provider"},
			)
		self.assertIn("error", result)

	def test_administrator_succeeds(self):
		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = dispatch.dispatch_tool(tool="read", user="Administrator", arguments={"doctype": "DocType"})
		self.assertIn("result", result)

	def test_disabled_tool_rejected(self):
		frappe.db.set_value("AI Tool", "read", "enabled", 0)
		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			with self.assertRaises(frappe.ValidationError):
				dispatch.dispatch_tool(tool="read", user="Administrator", arguments={"doctype": "DocType"})

	def test_unknown_user_rejected(self):
		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			with self.assertRaises(frappe.DoesNotExistError):
				dispatch.dispatch_tool(tool="read", user="not-a-real-user@example.com", arguments={})

	def test_tool_exception_returned_as_error_not_raised(self):
		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = dispatch.dispatch_tool(
				tool="read", user="Administrator", arguments={"doctype": "Not A Real DocType"}
			)
		self.assertIn("error", result)


class TestDispatchPluginToolScope(IntegrationTestCase):
	"""Direct FAC context-sensitive tools must use the persisted run scope."""

	def setUp(self):
		self._original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = TEST_SECRET

	def tearDown(self):
		if self._original_secret is None:
			frappe.conf.pop("frappe_ai_service_secret", None)
		else:
			frappe.conf.frappe_ai_service_secret = self._original_secret
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _run_with_knowledge_scope(self) -> tuple[str, str]:
		if not frappe.db.exists("AI Provider", "openai"):
			frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
		if not frappe.db.exists("AI Model", "FAC Scoped Model"):
			frappe.get_doc(
				{
					"doctype": "AI Model",
					"title": "FAC Scoped Model",
					"provider": "openai",
					"model_id": "gpt-4o-mini",
				}
			).insert(ignore_permissions=True)
		agent = "FAC Scoped Agent"
		if not frappe.db.exists("AI Agent", agent):
			with patch("frappe_ai.frappe_ai.doctype.ai_agent.ai_agent.AIAgent._ensure_knowledge_search_tool"):
				frappe.get_doc(
					{
						"doctype": "AI Agent",
						"title": agent,
						"model": "FAC Scoped Model",
						"instructions": "Use scoped tools.",
					}
				).insert(ignore_permissions=True)
		with patch(
			"frappe_ai.frappe_ai.doctype.ai_settings.ai_settings.require_embedding_model"
		):
			knowledge_base = frappe.get_doc(
				{
					"doctype": "AI Knowledge Base",
					"title": "FAC Scoped Knowledge",
					"description": "Only this knowledge base is in scope.",
				}
			).insert(ignore_permissions=True)
		agent_doc = frappe.get_doc("AI Agent", agent)
		agent_doc.append("knowledge_bases", {"knowledge_base": knowledge_base.name})
		with patch("frappe_ai.frappe_ai.doctype.ai_agent.ai_agent.AIAgent._ensure_knowledge_search_tool"):
			agent_doc.save(ignore_permissions=True)
		session = frappe.get_doc(
			{"doctype": "AI Session", "agent": agent, "source": "Manual", "title": "FAC Scope Session"}
		).insert(ignore_permissions=True)
		run = create_run(source="Manual", input="scope", session=session.name, config_snapshot={})
		return run.name, knowledge_base.name

	def test_search_knowledge_uses_agent_knowledge_bases_not_model_arguments(self):
		run, knowledge_base = self._run_with_knowledge_scope()

		class Registry:
			def execute_tool(self, _name, arguments):
				return SearchKnowledgeTool().execute(arguments)

		with (
			patch("frappe_ai.api.dispatch._verify_service_secret"),
			patch("frappe_ai.api.budgets.consume"),
			patch("frappe_assistant_core.core.tool_registry.get_tool_registry", return_value=Registry()),
			patch("frappe_ai.knowledge.retriever.retrieve", return_value=[]) as retrieve,
		):
			result = dispatch.dispatch_plugin_tool(
				tool="search_knowledge",
				user="Administrator",
				arguments={"query": "hello", "__frappe_ai_knowledge_bases": ["attacker-kb"]},
				run=run,
			)

		self.assertIn("result", result)
		retrieve.assert_called_once_with("hello", kbs=[knowledge_base])

	def test_update_memory_uses_run_agent_not_model_arguments(self):
		run, _knowledge_base = self._run_with_knowledge_scope()

		class Registry:
			def execute_tool(self, _name, arguments):
				return UpdateMemoryTool().execute(arguments)

		with (
			patch("frappe_ai.api.dispatch._verify_service_secret"),
			patch("frappe_ai.api.budgets.consume"),
			patch("frappe_assistant_core.core.tool_registry.get_tool_registry", return_value=Registry()),
			patch("frappe_ai.memory.memory.save_memory", return_value={"memory_id": "MEM-1"}) as save_memory,
		):
			result = dispatch.dispatch_plugin_tool(
				tool="update_memory",
				user="Administrator",
				arguments={"content": "fact", "scope": "agent", "agent": "attacker-agent"},
				run=run,
			)

		self.assertIn("result", result)
		save_memory.assert_called_once_with(
			"FAC Scoped Agent",
			content="fact",
			scope="agent",
			memory_id=None,
			keywords=None,
			source_run=run,
		)


class TestGetAgentTools(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_returns_confirmation_map(self):
		sync_builtin_tools()
		agent = _model_and_agent("Tools Map Agent")
		mapping = api.get_agent_tools(agent)
		self.assertIn("read", mapping)
		self.assertFalse(mapping["read"])

	def test_empty_agent_name_returns_empty(self):
		self.assertEqual(api.get_agent_tools(""), {})


class TestStartRunValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_empty_input_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Input"):
			api.start_run(input="   ", agent=_model_and_agent())

	def test_missing_agent_for_new_session_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Agent"):
			api.start_run(input="hello")

	def test_start_run_creates_session_and_run_and_mints_token(self):
		agent = _model_and_agent("Start Run Agent")
		result = api.start_run(input="hello there", agent=agent)

		self.assertIn("run", result)
		self.assertIn("session", result)
		self.assertIn("token", result)
		self.assertIn("stream_url", result)
		self.assertEqual(frappe.get_value("AI Run", result["run"], "status"), "Running")
		self.assertEqual(frappe.get_value("AI Session", result["session"], "agent"), agent)

	def test_start_run_switches_session_model(self):
		agent = _model_and_agent("Model Switch Agent")
		other_model = "Model Switch Other Model"
		if not frappe.db.exists("AI Model", other_model):
			frappe.get_doc(
				{"doctype": "AI Model", "title": other_model, "provider": "openai", "model_id": "gpt-4o-mini"}
			).insert(ignore_permissions=True)

		first = api.start_run(input="hello", agent=agent)
		frappe.db.set_value("AI Run", first["run"], "status", "Completed")

		second = api.start_run(input="switch please", session=first["session"], model=other_model)

		self.assertEqual(frappe.get_value("AI Session", first["session"], "model"), other_model)
		config_snapshot = frappe.get_value("AI Run", second["run"], "config_snapshot")
		self.assertIn(other_model, config_snapshot)

	def test_start_run_model_switch_rejects_disabled_model(self):
		agent = _model_and_agent("Model Switch Disabled Agent")
		disabled_model = "Model Switch Disabled Model"
		if not frappe.db.exists("AI Model", disabled_model):
			frappe.get_doc(
				{
					"doctype": "AI Model",
					"title": disabled_model,
					"provider": "openai",
					"model_id": "gpt-4o-mini",
					"enabled": 0,
				}
			).insert(ignore_permissions=True)

		first = api.start_run(input="hello", agent=agent)
		frappe.db.set_value("AI Run", first["run"], "status", "Completed")

		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			api.start_run(input="switch please", session=first["session"], model=disabled_model)

	def test_start_run_model_switch_blocked_while_run_in_progress(self):
		agent = _model_and_agent("Model Switch Blocked Agent")
		other_model = "Model Switch Blocked Other Model"
		if not frappe.db.exists("AI Model", other_model):
			frappe.get_doc(
				{"doctype": "AI Model", "title": other_model, "provider": "openai", "model_id": "gpt-4o-mini"}
			).insert(ignore_permissions=True)

		first = api.start_run(input="hello", agent=agent)
		# `first`'s run is left "Running" (start_run's default), so the session is blocked.

		with self.assertRaises(frappe.ValidationError):
			api.start_run(input="switch please", session=first["session"], model=other_model)

	def test_start_run_model_switch_requires_permission(self):
		# "AI Model" grants read to role "All", so denial has to come from a User
		# Permission restricting this guest to a *different* AI Model record —
		# mirroring how record-level restriction is normally configured in Frappe.
		agent = _model_and_agent("Model Switch Permission Agent")
		restricted_model = "Model Switch Restricted Model"
		if not frappe.db.exists("AI Model", restricted_model):
			frappe.get_doc(
				{
					"doctype": "AI Model",
					"title": restricted_model,
					"provider": "openai",
					"model_id": "gpt-4o-mini",
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("User", "test-model-switch-guest@example.com"):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": "test-model-switch-guest@example.com",
					"first_name": "Model Switch Guest",
					"send_welcome_email": 0,
					"roles": [],
				}
			).insert(ignore_permissions=True)
		allowed_model = frappe.get_value("AI Agent", agent, "model")
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": "test-model-switch-guest@example.com",
				"allow": "AI Model",
				"for_value": allowed_model,
			}
		).insert(ignore_permissions=True)

		first = api.start_run(input="hello", agent=agent)
		frappe.db.set_value("AI Run", first["run"], "status", "Completed")
		frappe.db.set_value("AI Session", first["session"], "owner", "test-model-switch-guest@example.com")

		frappe.set_user("test-model-switch-guest@example.com")
		try:
			with self.assertRaises(frappe.PermissionError):
				api.start_run(input="switch please", session=first["session"], model=restricted_model)
		finally:
			frappe.set_user("Administrator")


class TestStopRunAndFeedback(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_stop_run_marks_failed(self):
		agent = _model_and_agent("Stop Run Agent")
		started = api.start_run(input="hello", agent=agent)
		result = api.stop_run(started["run"])
		self.assertEqual(result["status"], "Failed")

	def test_submit_feedback_on_completed_run(self):
		agent = _model_and_agent("Feedback Agent")
		started = api.start_run(input="hello", agent=agent)
		frappe.db.set_value("AI Run", started["run"], "status", "Completed")

		result = api.submit_feedback(started["run"], "Up", "great answer")
		self.assertEqual(result["rating"], "Up")

	def test_submit_feedback_down_saves_shared_memory_when_enabled(self):
		sync_builtin_tools()
		agent = _model_and_agent("Feedback Memory Agent")
		agent_doc = frappe.get_doc("AI Agent", agent)
		if not any(row.tool == "update_memory" for row in agent_doc.tools):
			agent_doc.append("tools", {"tool": "update_memory"})
			agent_doc.save(ignore_permissions=True)
		started = api.start_run(input="hello", agent=agent)
		frappe.db.set_value("AI Run", started["run"], "status", "Completed")

		result = api.submit_feedback(started["run"], "Down", "Always mention the project code.")

		self.assertEqual(result["rating"], "Down")
		rows = frappe.get_all(
			"AI Agent Memory",
			filters={"agent": agent, "source": "Feedback"},
			fields=["content", "scope"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].scope, "Agent")
		self.assertEqual(rows[0].content, "Always mention the project code.")

	def test_submit_feedback_invalid_rating_rejected(self):
		agent = _model_and_agent("Bad Feedback Agent")
		started = api.start_run(input="hello", agent=agent)
		frappe.db.set_value("AI Run", started["run"], "status", "Completed")

		with self.assertRaisesRegex(frappe.ValidationError, "Rating"):
			api.submit_feedback(started["run"], "Sideways")


class TestPersistenceCallbacks(IntegrationTestCase):
	def setUp(self):
		self._original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = TEST_SECRET
		frappe.set_user("Administrator")

	def tearDown(self):
		if self._original_secret is None:
			frappe.conf.pop("frappe_ai_service_secret", None)
		else:
			frappe.conf.frappe_ai_service_secret = self._original_secret
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_persist_run_result_requires_service_secret(self):
		agent = _model_and_agent("Persist Agent")
		started = api.start_run(input="hello", agent=agent)

		with patch("frappe.get_request_header", new=_patch_request_header(None)):
			with self.assertRaises(frappe.AuthenticationError):
				api.persist_run_result(started["run"], {"status": "Completed", "iterations": 1, "messages": []})

	def test_fail_run_requires_service_secret(self):
		agent = _model_and_agent("Fail Agent")
		started = api.start_run(input="hello", agent=agent)

		with patch("frappe.get_request_header", new=_patch_request_header(None)):
			with self.assertRaises(frappe.AuthenticationError):
				api.fail_run(started["run"], "boom")

	def test_fail_run_with_valid_secret(self):
		agent = _model_and_agent("Fail Agent 2")
		started = api.start_run(input="hello", agent=agent)

		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = api.fail_run(started["run"], "boom")
		self.assertEqual(result["status"], "Failed")

	def test_fail_run_with_valid_secret_works_from_guest_entry(self):
		agent = _model_and_agent("Fail Agent Guest")
		started = api.start_run(input="hello", agent=agent)
		frappe.set_user("Guest")

		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = api.fail_run(started["run"], "boom")

		self.assertEqual(result["status"], "Failed")

	def test_persist_run_result_with_valid_secret_works_from_guest_entry(self):
		agent = _model_and_agent("Persist Agent Guest")
		started = api.start_run(input="hello", agent=agent)
		frappe.set_user("Guest")

		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = api.persist_run_result(started["run"], {"status": "Completed", "iterations": 1, "messages": []})

		self.assertEqual(result["status"], "Completed")


class TestFrontendAPI(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_bootstrap_returns_frontend_state(self):
		agent = _model_and_agent("Frontend Bootstrap Agent")
		if not frappe.db.exists("AI MCP Connection", "frontend-bootstrap-mcp"):
			frappe.get_doc(
				{
					"doctype": "AI MCP Connection",
					"connection_name": "Frontend Bootstrap MCP",
					"connection_type": "SSE",
					"endpoint_url": "https://example.com/mcp",
					"enabled": 1,
					"is_connected": 1,
					"status_message": "Healthy",
				}
			).insert(ignore_permissions=True)
		agent_doc = frappe.get_doc("AI Agent", agent)
		if not any(row.mcp_connection == "frontend-bootstrap-mcp" for row in agent_doc.mcp_connections):
			agent_doc.append("mcp_connections", {"mcp_connection": "frontend-bootstrap-mcp"})
			agent_doc.save(ignore_permissions=True)

		data = frontend.bootstrap()

		self.assertEqual(data["user"]["name"], frappe.session.user)
		agent_row = next(row for row in data["agent"]["items"] if row["name"] == agent)
		self.assertEqual(agent_row["mcp_connections"][0]["name"], "frontend-bootstrap-mcp")
		self.assertEqual(agent_row["mcp_connections"][0]["status"], "connected")
		self.assertGreaterEqual(agent_row["tools"]["count"], 1)
		self.assertTrue(any(row["name"] == data["agent"]["selected"] for row in data["agent"]["items"]))
		self.assertIsInstance(data["session"]["history"], list)
		self.assertIsInstance(data["composer"]["supported_file_types"], list)
		self.assertIn("custom_frontend", data["capabilities"])

	def test_sessions_and_session_detail_normalize_transcript(self):
		agent = _model_and_agent("Frontend Detail Agent")
		started = api.start_run(input="Find this frontend session", agent=agent)
		session_doc = frappe.get_doc("AI Session", started["session"])
		session_doc.append_run_messages(
			[
				{
					"role": "assistant",
					"content": "Here is the answer.",
					"tool_calls": [
						{
							"id": "call_1",
							"type": "function",
							"function": {"name": "read", "arguments": "{\"doctype\":\"DocType\"}"},
						}
					],
				},
				{
					"role": "tool",
					"tool_call_id": "call_1",
					"content": "{\"status\":\"approved\"}",
				},
			],
			started["run"],
		)
		frappe.db.set_value(
			"AI Run",
			started["run"],
			{
				"status": "Paused",
				"questions": json.dumps(
					[
						{
							"key": "call_1",
							"name": "read",
							"prompt": "Allow this tool call?",
							"arguments": {"doctype": "DocType"},
						}
					]
				),
				"feedback_rating": "Up",
				"feedback_comment": "Looks good",
			},
		)

		rows = frontend.sessions(query="Find this frontend")
		self.assertTrue(any(row["name"] == started["session"] for row in rows["session"]["history"]))

		detail = frontend.session_detail(started["session"])
		self.assertEqual(detail["session"]["current"]["name"], started["session"])
		self.assertEqual(detail["agent"]["selected"], agent)
		self.assertTrue(any(message["role"] == "user" for message in detail["execution"]["transcript"]))
		self.assertTrue(any(message["role"] == "assistant" for message in detail["execution"]["transcript"]))
		self.assertEqual(detail["execution"]["paused_run"]["run"], started["run"])
		self.assertEqual(detail["execution"]["paused_run"]["questions"][0]["key"], "call_1")
		self.assertEqual(detail["execution"]["feedback"][0]["run"], started["run"])
		self.assertEqual(detail["execution"]["feedback"][0]["rating"], "Up")
		assistant_message = next(
			item for item in detail["execution"]["transcript"] if item["role"] == "assistant" and item["run"] == started["run"]
		)
		self.assertEqual(assistant_message["executions"][0]["id"], "call_1")
		self.assertEqual(assistant_message["executions"][0]["status"], "awaiting_confirmation")
		self.assertEqual(assistant_message["executions"][0]["result_summary"], "approved")
		self.assertEqual(detail["execution"]["current_run"]["status"], "Paused")

	def test_frontend_start_run_returns_stream_bootstrap(self):
		agent = _model_and_agent("Frontend Start Agent")

		result = frontend.start_run(input="hello from frontend", agent=agent)

		self.assertIn("run", result)
		self.assertIn("session", result)
		self.assertIn("token", result)
		self.assertIn("stream_url", result)

	def test_frontend_resume_run_returns_stream_bootstrap(self):
		agent = _model_and_agent("Frontend Resume Agent")
		started = api.start_run(input="hello from frontend", agent=agent)
		frappe.db.set_value(
			"AI Run",
			started["run"],
			{
				"status": "Paused",
				"questions": json.dumps(
					[
						{
							"key": "call_1",
							"name": "read",
							"prompt": "Allow this tool call?",
							"arguments": {"doctype": "DocType"},
						}
					]
				),
			},
		)

		result = frontend.resume_run(started["run"], {"call_1": "Approve"})

		self.assertEqual(result["run"], started["run"])
		self.assertEqual(result["session"], started["session"])
		self.assertIn("token", result)
		self.assertIn("stream_url", result)

	def test_frontend_agent_tools_and_run_feedback_are_normalized(self):
		agent = _model_and_agent("Frontend Tool Meta Agent")
		started = api.start_run(input="hello from frontend", agent=agent)
		frappe.db.set_value(
			"AI Run",
			started["run"],
			{"status": "Completed", "feedback_rating": "Up", "feedback_comment": "Nice"},
		)

		tools = frontend.agent_tools(agent)
		feedback = frontend.run_feedback(started["run"])

		self.assertGreaterEqual(tools["tools"]["count"], 1)
		self.assertTrue(any(tool["name"] == "read" for tool in tools["tools"]["summaries"]))
		self.assertEqual(feedback["run"], started["run"])
		self.assertEqual(feedback["rating"], "Up")
		self.assertEqual(feedback["comment"], "Nice")

	def test_frontend_recover_session_returns_recovered_count(self):
		agent = _model_and_agent("Frontend Recover Agent")
		started = api.start_run(input="recover this frontend session", agent=agent)

		result = frontend.recover_session(started["session"])

		self.assertEqual(result["recovered"], 1)
		self.assertEqual(frappe.get_value("AI Run", started["run"], "status"), "Failed")

	def test_frontend_upload_attachment_returns_staged_attachment_shape(self):
		uploaded = SimpleNamespace(filename="notes.txt", stream=io.BytesIO(b"hello"))
		request = SimpleNamespace(files={"file": uploaded})
		saved = SimpleNamespace(name="FILE-0001")
		staged = {"file": "FILE-0001", "file_name": "notes.txt", "file_size": 5}

		with (
			patch("frappe.request", request),
			patch("frappe_ai.api.frontend.save_file", return_value=saved) as save_mock,
			patch("frappe_ai.api.frontend.api.attach_file", return_value=staged) as attach_mock,
		):
			result = frontend.upload_attachment()

		save_mock.assert_called_once()
		attach_mock.assert_called_once_with("FILE-0001")
		self.assertEqual(result, {"attachment": staged})
