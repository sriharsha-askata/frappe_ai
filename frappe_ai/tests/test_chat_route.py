# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agno.models.message import Message
from agno.run.agent import RunErrorEvent, RunOutput, RunStatus, ToolCallCompletedEvent

from frappe_ai.service.routes.chat import stream_chat


def _config(questions=None, tools=None):
	return {
		"model": {"provider": "google", "model_id": "gemini-2.5-flash"},
		"messages": [
			{"role": "system", "content": "Be helpful."},
			{"role": "user", "content": "continue"},
		],
		"questions": questions or [],
		"tools": tools if tools is not None else [{"name": "create_record", "source": "fac"}],
	}


def _client():
	return SimpleNamespace(
		persist_run_result=AsyncMock(),
		fail_run=AsyncMock(),
		get_fallback_model_config=AsyncMock(return_value=None),
		log_diagnostic=AsyncMock(),
		dispatch_plugin_tool=AsyncMock(return_value={"result": {"ok": True}}),
	)


class _ResumingAgent:
	async def arun(self, **_kwargs):
		yield RunOutput(
			content="The record is created.",
			messages=[Message(role="assistant", content="The record is created.")],
			status=RunStatus.completed,
		)


class TestStreamChatProviderAndConfirmation(unittest.IsolatedAsyncioTestCase):
	async def test_provider_failure_is_normalized_in_sse_and_persistence(self):
		class FailingAgent:
			async def arun(self, **_kwargs):
				yield RunErrorEvent(
					content="rate limit exceeded",
					error_type="RateLimitError",
					error_id="err_123",
					additional_data={"provider_request_id": "req_456"},
				)

		builder = SimpleNamespace(build=AsyncMock(return_value=(FailingAgent(), _config())))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			frames = [frame async for frame in stream_chat("RUN-1", "Administrator", "SES-1", client)]

		error = json.loads(frames[-1].split(b"data: ", 1)[1])
		self.assertEqual(error["code"], "rate_limit")
		self.assertIn("rate limit exceeded", error["message"])
		self.assertEqual(error["error_type"], "RateLimitError")
		self.assertEqual(error["error_id"], "err_123")
		self.assertEqual(error["additional_data"], {"provider_request_id": "req_456"})
		client.fail_run.assert_awaited_once_with("RUN-1", error["message"])

	async def test_confirmation_deny_persists_terminal_result_without_model_call(self):
		question = {"key": "call-1", "name": "delete_record", "arguments": {"name": "DOC-1"}, "prompt": "Delete?"}
		agent = SimpleNamespace(arun=AsyncMock())
		builder = SimpleNamespace(build=AsyncMock(return_value=(agent, _config([question]))))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			frames = [
				frame
				async for frame in stream_chat(
					"RUN-2", "Administrator", "SES-1", client, answers={"call-1": "Deny"}
				)
			]

		payload = json.loads(frames[-1].split(b"data: ", 1)[1])
		self.assertEqual(payload["status"], "Completed")
		agent.arun.assert_not_awaited()
		client.dispatch_plugin_tool.assert_not_awaited()
		client.persist_run_result.assert_awaited_once()
		stored = client.persist_run_result.await_args.args[1]["messages"]
		self.assertEqual([row["role"] for row in stored], ["system", "user", "assistant", "tool"])
		self.assertEqual(stored[-2]["tool_calls"][0]["id"], "call-1")
		self.assertEqual(stored[-1]["tool_call_id"], "call-1")
		self.assertEqual(json.loads(stored[-1]["content"]), {"status": "denied"})

	async def test_confirmation_approve_dispatches_and_resumes_model(self):
		question = {"key": "call-1", "name": "create_record", "arguments": {"title": "Created"}, "prompt": "Create?"}
		builder = SimpleNamespace(build=AsyncMock(return_value=(_ResumingAgent(), _config([question]))))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			frames = [
				frame
				async for frame in stream_chat(
					"RUN-3", "Administrator", "SES-1", client, answers={"call-1": "Approve"}
				)
			]

		# An Assistant Core tool resumes through `dispatch_plugin_tool`, the same
		# dispatcher the streaming path uses for `source == "fac"`. The run id must
		# be passed through: budgets fail closed without it, and a confirmation-gated
		# tool is exactly the mutating kind the budget bounds.
		client.dispatch_plugin_tool.assert_awaited_once_with(
			"create_record", "Administrator", {"title": "Created"}, "RUN-3", "call-1"
		)
		payload = json.loads(frames[-1].split(b"data: ", 1)[1])
		self.assertEqual(payload["status"], "Completed")
		self.assertEqual(payload["output"], "The record is created.")
		stored = client.persist_run_result.await_args.args[1]["messages"]
		self.assertEqual(stored[0]["tool_calls"][0]["id"], "call-1")
		self.assertEqual(stored[1]["tool_call_id"], "call-1")
		self.assertEqual(json.loads(stored[1]["content"]), {"ok": True})

	async def test_confirmation_redirect_reconstructs_tool_pair_for_model(self):
		question = {"key": "call-1", "name": "create_record", "arguments": {"title": "Original"}, "prompt": "Create?"}
		seen_messages = []

		class RedirectAgent:
			async def arun(self, **kwargs):
				seen_messages.extend(kwargs["input"])
				yield RunOutput(content="Adjusted.", messages=[Message(role="assistant", content="Adjusted.")], status=RunStatus.completed)

		builder = SimpleNamespace(build=AsyncMock(return_value=(RedirectAgent(), _config([question]))))
		client = _client()
		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			[frame async for frame in stream_chat("RUN-6", "Administrator", "SES-1", client, answers={"call-1": "Use a different title"})]

		self.assertEqual(seen_messages[-2].role, "assistant")
		self.assertEqual(seen_messages[-2].tool_calls[0]["id"], "call-1")
		self.assertEqual(seen_messages[-1].tool_call_id, "call-1")
		self.assertEqual(json.loads(seen_messages[-1].content)["user_feedback"], "Use a different title")
		client.dispatch_plugin_tool.assert_not_awaited()

	async def test_provider_failure_after_completed_tool_does_not_retry(self):
		class ToolThenFailureAgent:
			async def arun(self, **_kwargs):
				yield ToolCallCompletedEvent(
					tool=SimpleNamespace(
						tool_call_id="call-1", tool_name="create_record", tool_args={},
						tool_call_error=False, result={"ok": True},
					),
					content="completed",
				)
				yield RunErrorEvent(content="provider failed")

		builder = SimpleNamespace(build=AsyncMock(return_value=(ToolThenFailureAgent(), _config())))
		client = _client()
		client.get_fallback_model_config.return_value = {"model_id": "other-model"}

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			frames = [frame async for frame in stream_chat("RUN-7", "Administrator", "SES-1", client)]

		client.get_fallback_model_config.assert_not_awaited()
		client.fail_run.assert_awaited_once()
		client.persist_run_result.assert_not_awaited()
		self.assertIn(b"event: tool_ended", frames[1])
		self.assertIn(b"event: error", frames[-1])

	async def test_approved_non_fac_tool_is_not_dispatched(self):
		question = {"key": "call-1", "name": "create_record", "arguments": {"title": "Created"}, "prompt": "Create?"}
		config = _config([question], tools=[{"name": "create_record", "source": "manual"}])
		builder = SimpleNamespace(build=AsyncMock(return_value=(_ResumingAgent(), config)))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			[
				frame
				async for frame in stream_chat(
					"RUN-4", "Administrator", "SES-1", client, answers={"call-1": "Approve"}
				)
			]

		client.dispatch_plugin_tool.assert_not_awaited()

	async def test_approved_call_for_unbound_tool_is_not_dispatched(self):
		"""An approval cannot re-grant a tool the agent no longer binds.

		The pending call was recorded while the tool was bound; if it was unbound
		before the resume, `config["tools"]` no longer lists it and neither
		dispatcher may run it.
		"""
		question = {"key": "call-1", "name": "delete_record", "arguments": {"name": "DOC-1"}, "prompt": "Delete?"}
		config = _config([question], tools=[{"name": "create_record", "source": "fac"}])
		builder = SimpleNamespace(build=AsyncMock(return_value=(_ResumingAgent(), config)))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			[
				frame
				async for frame in stream_chat(
					"RUN-5", "Administrator", "SES-1", client, answers={"call-1": "Approve"}
				)
			]

		client.dispatch_plugin_tool.assert_not_awaited()


if __name__ == "__main__":
	unittest.main()
