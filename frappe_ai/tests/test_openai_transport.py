# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import builtins
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from agno.models.message import Message
from agno.tools.function import Function

from frappe_ai.lib.model import (
	PROVIDER_ENDPOINT_DEFAULTS,
	create_openai_compatible_model,
	normalize_provider_error,
	resolve_model_config,
)


class TestOpenAICompatibleTransport(unittest.TestCase):
	def test_tool_calls_round_trip_through_shared_transport(self):
		requests: list[dict] = []

		def handler(request: httpx.Request) -> httpx.Response:
			payload = json.loads(request.content)
			requests.append(payload)
			if any(message.get("role") == "tool" for message in payload["messages"]):
				content = "tool complete"
				message = {"role": "assistant", "content": content}
			else:
				message = {
					"role": "assistant",
					"content": None,
					"tool_calls": [
						{
							"id": "call_1",
							"type": "function",
							"function": {"name": "lookup", "arguments": '{"value": 2}'},
						}
					],
				}
			return httpx.Response(
				200,
				json={
					"id": "chatcmpl-tool",
					"object": "chat.completion",
					"created": 1,
					"model": "test-model",
					"choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
					"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
				},
			)

		client = httpx.Client(transport=httpx.MockTransport(handler))
		model = create_openai_compatible_model(
			{
				"provider": "groq",
				"model_id": "test-model",
				"api_key": "test-key",
				"base_url": PROVIDER_ENDPOINT_DEFAULTS["groq"],
				"params": {"http_client": client},
			}
		)
		tool = Function(
			name="lookup",
			description="Look up a value.",
			parameters={"type": "object", "properties": {"value": {"type": "integer"}}},
			entrypoint=lambda value: {"value": value},
		)

		response = model.response(messages=[Message(role="user", content="look up 2")], tools=[tool])

		self.assertEqual(response.content, "tool complete")
		self.assertEqual(len(requests), 2)
		self.assertTrue(requests[0]["tools"])
		self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
		client.close()

	def test_structured_output_parameter_is_forwarded(self):
		requests: list[dict] = []

		def handler(request: httpx.Request) -> httpx.Response:
			requests.append(json.loads(request.content))
			return httpx.Response(
				200,
				json={
					"id": "chatcmpl-json",
					"object": "chat.completion",
					"created": 1,
					"model": "test-model",
					"choices": [
						{
							"index": 0,
							"message": {"role": "assistant", "content": '{"answer":"ok"}'},
							"finish_reason": "stop",
						}
					],
				},
			)

		client = httpx.Client(transport=httpx.MockTransport(handler))
		model = create_openai_compatible_model(
			{
				"provider": "openai",
				"model_id": "test-model",
				"api_key": "test-key",
				"base_url": PROVIDER_ENDPOINT_DEFAULTS["openai"],
				"params": {"http_client": client},
			}
		)
		response = model.response(
			messages=[Message(role="user", content="return JSON")], response_format={"type": "json_object"}
		)

		self.assertEqual(response.content, '{"answer":"ok"}')
		self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
		client.close()

	def test_streaming_completion_is_parsed_by_agno(self):
		chunks = [
			{
				"id": "chatcmpl-stream",
				"object": "chat.completion.chunk",
				"created": 1,
				"model": "test-model",
				"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}, "finish_reason": None}],
			},
			{
				"id": "chatcmpl-stream",
				"object": "chat.completion.chunk",
				"created": 1,
				"model": "test-model",
				"choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": "stop"}],
			},
		]
		body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"

		def handler(_request: httpx.Request) -> httpx.Response:
			return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

		client = httpx.Client(transport=httpx.MockTransport(handler))
		model = create_openai_compatible_model(
			{
				"provider": "openai",
				"model_id": "test-model",
				"api_key": "test-key",
				"base_url": PROVIDER_ENDPOINT_DEFAULTS["openai"],
				"params": {"http_client": client},
			}
		)

		responses = list(model.response_stream(messages=[Message(role="user", content="hello")]))

		self.assertEqual("".join(response.content or "" for response in responses), "hello world")
		client.close()

	def test_openai_google_and_groq_completion_flows_share_transport(self):
		seen_paths: list[str] = []

		def handler(request: httpx.Request) -> httpx.Response:
			seen_paths.append(str(request.url))
			return httpx.Response(
				200,
				json={
					"id": "chatcmpl-test",
					"object": "chat.completion",
					"created": 1,
					"model": "test-model",
					"choices": [
						{
							"index": 0,
							"message": {"role": "assistant", "content": "hello"},
							"finish_reason": "stop",
						}
					],
					"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
				},
			)

		for provider in ("openai", "google", "groq"):
			with self.subTest(provider=provider):
				client = httpx.Client(transport=httpx.MockTransport(handler))
				model = create_openai_compatible_model(
					{
						"provider": provider,
						"model_id": "test-model",
						"api_key": "test-key",
						"base_url": PROVIDER_ENDPOINT_DEFAULTS[provider],
						"params": {"http_client": client},
					}
				)
				response = model.response(messages=[Message(role="user", content="hello")])
				self.assertEqual(response.content, "hello")
				client.close()

		self.assertEqual(len(seen_paths), 3)
		self.assertTrue(all(path.endswith("/chat/completions") for path in seen_paths))

	def test_google_does_not_import_google_genai(self):
		real_import = builtins.__import__

		def guarded_import(name, *args, **kwargs):
			if name == "google.genai" or name.startswith("google.genai."):
				raise AssertionError("native Google SDK must not be imported")
			return real_import(name, *args, **kwargs)

		with patch("builtins.__import__", side_effect=guarded_import):
			create_openai_compatible_model(
				{
					"provider": "google",
					"model_id": "gemini-2.5-flash",
					"api_key": "gemini-key",
					"base_url": PROVIDER_ENDPOINT_DEFAULTS["google"],
					"params": {},
				}
			)

	def test_google_uses_openai_sdk_transport_and_extra_body(self):
		model = create_openai_compatible_model(
			{
				"provider": "google",
				"model_id": "gemini-2.5-flash",
				"api_key": "gemini-key",
				"base_url": PROVIDER_ENDPOINT_DEFAULTS["google"],
				"params": {"extra_body": {"thinking_config": {"thinking_budget": 0}}},
			}
		)

		self.assertEqual(model.__class__.__name__, "OpenAIChat")
		self.assertEqual(model.base_url, PROVIDER_ENDPOINT_DEFAULTS["google"])
		self.assertEqual(model.extra_body["thinking_config"]["thinking_budget"], 0)

	def test_retry_count_is_bounded(self):
		model = create_openai_compatible_model(
			{"provider": "groq", "model_id": "llama", "params": {"max_retries": 99, "retries": 99}}
		)
		self.assertEqual(model.max_retries, 2)
		self.assertEqual(model.retries, 2)

	def test_linked_provider_credentials_win_and_model_params_override(self):
		provider = SimpleNamespace(
			enabled=1,
			base_url="https://provider.example/v1",
			extra_params='{"temperature": 0.1, "extra_body": {"provider": "default"}}',
		)
		provider.get_password = lambda *args, **kwargs: "provider-key"
		model = SimpleNamespace(
			provider="google",
			model_id="gemini-2.5-flash",
			api_key="model-key",
			base_url="https://model.example/v1",
			params='{"temperature": 0.2, "extra_body": {"provider": "model"}}',
		)
		model.get_password = lambda *args, **kwargs: "model-key"

		with patch("frappe.get_doc", return_value=provider):
			config = resolve_model_config(model)

		self.assertEqual(config["api_key"], "provider-key")
		self.assertEqual(config["base_url"], "https://provider.example/v1")
		self.assertEqual(config["params"]["temperature"], 0.2)
		self.assertEqual(config["params"]["extra_body"], {"provider": "model"})

	def test_google_provider_gets_compatibility_endpoint_default(self):
		provider = SimpleNamespace(enabled=1, base_url=None, extra_params=None)
		provider.get_password = lambda *args, **kwargs: "gemini-key"
		model = SimpleNamespace(
			provider="google",
			model_id="gemini-2.5-flash",
			params=None,
		)

		with patch("frappe.get_doc", return_value=provider):
			config = resolve_model_config(model)

		self.assertEqual(config["base_url"], PROVIDER_ENDPOINT_DEFAULTS["google"])

	def test_timeout_and_rate_limit_are_normalized(self):
		timeout = normalize_provider_error(TimeoutError("request timed out"), provider="groq")
		rate_limit = normalize_provider_error(
			type("RateLimit", (), {"status_code": 429, "message": "too many requests"})(), provider="groq"
		)

		self.assertEqual(timeout.code, "timeout")
		self.assertTrue(timeout.retryable)
		self.assertEqual(rate_limit.code, "rate_limit")
		self.assertTrue(rate_limit.retryable)


if __name__ == "__main__":
	unittest.main()
