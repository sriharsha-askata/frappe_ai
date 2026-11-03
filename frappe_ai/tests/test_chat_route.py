# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agno.models.message import Message
from agno.run.agent import RunContentEvent, RunErrorEvent, RunOutput, RunStatus, ToolCallCompletedEvent

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
		question = {
			"key": "call-1",
			"name": "delete_record",
			"arguments": {"name": "DOC-1"},
			"prompt": "Delete?",
		}
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
		question = {
			"key": "call-1",
			"name": "create_record",
			"arguments": {"title": "Created"},
			"prompt": "Create?",
		}
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
		question = {
			"key": "call-1",
			"name": "create_record",
			"arguments": {"title": "Original"},
			"prompt": "Create?",
		}
		seen_messages = []

		class RedirectAgent:
			async def arun(self, **kwargs):
				seen_messages.extend(kwargs["input"])
				yield RunOutput(
					content="Adjusted.",
					messages=[Message(role="assistant", content="Adjusted.")],
					status=RunStatus.completed,
				)

		builder = SimpleNamespace(build=AsyncMock(return_value=(RedirectAgent(), _config([question]))))
		client = _client()
		with patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder):
			[
				frame
				async for frame in stream_chat(
					"RUN-6", "Administrator", "SES-1", client, answers={"call-1": "Use a different title"}
				)
			]

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
						tool_call_id="call-1",
						tool_name="create_record",
						tool_args={},
						tool_call_error=False,
						result={"ok": True},
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
		question = {
			"key": "call-1",
			"name": "create_record",
			"arguments": {"title": "Created"},
			"prompt": "Create?",
		}
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
		question = {
			"key": "call-1",
			"name": "delete_record",
			"arguments": {"name": "DOC-1"},
			"prompt": "Delete?",
		}
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


def _text_of(frame: bytes) -> str:
	return json.loads(frame.split(b"data: ", 1)[1])["content"]


def _completed_output(text: str) -> RunOutput:
	return RunOutput(
		content=text, messages=[Message(role="assistant", content=text)], status=RunStatus.completed
	)


class TestStreamChatLiveStreaming(unittest.IsolatedAsyncioTestCase):
	"""Frames reach the client while the model is still working, and a retry never repeats them."""

	def _run(self, agent, client, **kwargs):
		"""`stream_chat` for `agent`; the builder stays patched until the test ends, since the stream is lazy."""
		builder = SimpleNamespace(
			build=AsyncMock(return_value=(agent, _config())), _build_model=lambda config: "fallback-model"
		)
		patcher = patch("frappe_ai.service.routes.chat.AgentBuilder", return_value=builder)
		patcher.start()
		self.addCleanup(patcher.stop)
		return stream_chat("RUN-L", "Administrator", "SES-1", client, **kwargs)

	async def test_text_is_sent_before_the_turn_ends(self):
		finish = asyncio.Event()

		class SlowAgent:
			async def arun(self, **_kwargs):
				yield RunContentEvent(content="Hello ")
				await finish.wait()
				yield _completed_output("Hello there.")

		stream = self._run(SlowAgent(), _client())
		self.assertIn(b"event: run_started", await anext(stream))
		first_text = await asyncio.wait_for(anext(stream), timeout=2)

		self.assertEqual(_text_of(first_text), "Hello ")
		self.assertFalse(finish.is_set(), "the text arrived while the model was still running")
		finish.set()
		rest = [frame async for frame in stream]
		self.assertIn(b"event: done", rest[-1])

	async def test_a_model_that_fails_before_any_output_is_retried_on_the_fallback(self):
		class FailingAgent:
			async def arun(self, **_kwargs):
				yield RunErrorEvent(content="provider down")

		class FallbackAgent:
			async def arun(self, **_kwargs):
				yield RunContentEvent(content="From the fallback.")
				yield _completed_output("From the fallback.")

		client = _client()
		client.get_fallback_model_config.return_value = {"model_id": "other-model"}
		with patch("frappe_ai.service.routes.chat._fallback_agent", return_value=FallbackAgent()):
			frames = [frame async for frame in self._run(FailingAgent(), client)]

		texts = [_text_of(frame) for frame in frames if b"event: text" in frame]
		self.assertEqual(texts, ["From the fallback."])
		self.assertIn(b"event: done", frames[-1])
		client.fail_run.assert_not_awaited()
		client.persist_run_result.assert_awaited_once()

	async def test_a_model_that_fails_after_sending_text_is_not_retried(self):
		class PartialThenFailure:
			async def arun(self, **_kwargs):
				yield RunContentEvent(content="Partial answer")
				yield RunErrorEvent(content="connection dropped")

		client = _client()
		client.get_fallback_model_config.return_value = {"model_id": "other-model"}
		frames = [frame async for frame in self._run(PartialThenFailure(), client)]

		client.get_fallback_model_config.assert_not_awaited()
		self.assertEqual([_text_of(f) for f in frames if b"event: text" in f], ["Partial answer"])
		self.assertIn(b"event: error", frames[-1])
		client.fail_run.assert_awaited_once()

	async def test_a_client_disconnect_fails_the_run_and_closes_the_agent(self):
		state = {"closed": False}

		class NeverFinishes:
			async def arun(self, **_kwargs):
				try:
					yield RunContentEvent(content="Working")
					await asyncio.Event().wait()
				finally:
					state["closed"] = True

		client = _client()
		stream = self._run(NeverFinishes(), client)
		await anext(stream)  # run_started
		await anext(stream)  # the first text frame
		await stream.aclose()  # the browser went away

		self.assertTrue(state["closed"], "the agent generator was closed")
		client.fail_run.assert_awaited_once()
		self.assertIn("disconnected", client.fail_run.await_args.args[1])
		client.persist_run_result.assert_not_awaited()


if __name__ == "__main__":
	unittest.main()
