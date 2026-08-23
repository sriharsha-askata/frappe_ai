# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agno.models.message import Message
from agno.run.agent import RunErrorEvent, RunOutput, RunStatus

from frappe_ai.service.routes.chat import stream_chat


def _config(questions=None):
	return {
		"model": {"provider": "google", "model_id": "gemini-2.5-flash"},
		"messages": [
			{"role": "system", "content": "Be helpful."},
			{"role": "user", "content": "continue"},
		],
		"questions": questions or [],
	}


def _client():
	return SimpleNamespace(
		persist_run_result=AsyncMock(),
		fail_run=AsyncMock(),
		dispatch_tool=AsyncMock(return_value={"result": {"ok": True}}),
	)


class TestStreamChatProviderAndConfirmation(unittest.IsolatedAsyncioTestCase):
	async def test_provider_failure_is_normalized_in_sse_and_persistence(self):
		class FailingAgent:
			async def arun(self, **_kwargs):
				yield RunErrorEvent(content="rate limit exceeded")

		builder = SimpleNamespace(build=AsyncMock(return_value=(FailingAgent(), _config())))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			frames = [frame async for frame in stream_chat("RUN-1", "Administrator", "SES-1", client)]

		error = json.loads(frames[-1].split(b"data: ", 1)[1])
		self.assertEqual(error["code"], "rate_limit")
		self.assertIn("rate limit exceeded", error["message"])
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
		client.dispatch_tool.assert_not_awaited()
		client.persist_run_result.assert_awaited_once()

	async def test_confirmation_approve_dispatches_and_resumes_model(self):
		question = {"key": "call-1", "name": "create_record", "arguments": {"title": "Created"}, "prompt": "Create?"}

		class ResumingAgent:
			async def arun(self, **_kwargs):
				yield RunOutput(
					content="The record is created.",
					messages=[Message(role="assistant", content="The record is created.")],
					status=RunStatus.completed,
				)

		builder = SimpleNamespace(build=AsyncMock(return_value=(ResumingAgent(), _config([question]))))
		client = _client()

		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			frames = [
				frame
				async for frame in stream_chat(
					"RUN-3", "Administrator", "SES-1", client, answers={"call-1": "Approve"}
				)
			]

		client.dispatch_tool.assert_awaited_once_with("create_record", "Administrator", {"title": "Created"})
		payload = json.loads(frames[-1].split(b"data: ", 1)[1])
		self.assertEqual(payload["status"], "Completed")
		self.assertEqual(payload["output"], "The record is created.")


if __name__ == "__main__":
	unittest.main()
