# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from frappe_ai.frappe_ai.doctype.ai_model.connection_test import (
	EMBEDDING_BATCH_INPUTS,
	SYNTHETIC_TOOL_NAME,
	run_capability_suite,
)
from frappe_ai.lib.model import PROVIDER_ENDPOINT_DEFAULTS


class TestConnectionSuiteHTTP(unittest.TestCase):
	def test_chat_suite_uses_expected_openai_compatible_requests(self):
		requests: list[dict] = []

		def handler(request: httpx.Request) -> httpx.Response:
			payload = json.loads(request.content)
			requests.append(payload)
			if payload.get("stream"):
				chunks = [
					{
						"id": "stream",
						"object": "chat.completion.chunk",
						"created": 1,
						"model": "test-model",
						"choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
					}
				]
				body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
				return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

			if payload.get("tools") and payload.get("tool_choice") != "none":
				if any(message.get("role") == "tool" for message in payload["messages"]):
					message = {"role": "assistant", "content": "The synthetic result was received."}
				else:
					message = {
						"role": "assistant",
						"content": None,
						"tool_calls": [
							{
								"id": "probe_call",
								"type": "function",
								"function": {"name": SYNTHETIC_TOOL_NAME, "arguments": "{}"},
							}
						],
					}
			else:
				message = {"role": "assistant", "content": '{"answer":"OK"}' if payload.get("response_format") else "OK"}

			return httpx.Response(
				200,
				json={
					"id": "completion",
					"object": "chat.completion",
					"created": 1,
					"model": "test-model",
					"choices": [
						{
							"index": 0,
							"message": message,
							"finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
						}
					],
				},
			)

		client = httpx.Client(transport=httpx.MockTransport(handler))
		result = run_capability_suite(
			{
				"provider": "openai",
				"model_id": "test-model",
				"api_key": "test-key",
				"base_url": PROVIDER_ENDPOINT_DEFAULTS["openai"],
				"params": {"http_client": client},
			}
		)
		client.close()

		self.assertTrue(result["ok"])
		self.assertEqual(len(requests), 7)  # basic + stream + declaration + 2-call round trip + 2 optional
		self.assertFalse(requests[0].get("stream", False))
		self.assertTrue(requests[1]["stream"])
		self.assertEqual(requests[2]["tool_choice"], "none")
		self.assertEqual(len(requests[2]["tools"]), 1)
		self.assertEqual(requests[2]["tools"][0]["function"]["name"], SYNTHETIC_TOOL_NAME)
		self.assertEqual(requests[3]["tool_choice"]["function"]["name"], SYNTHETIC_TOOL_NAME)
		self.assertEqual(requests[4]["messages"][-1]["role"], "tool")
		self.assertEqual(requests[5]["response_format"], {"type": "json_object"})
		self.assertGreater(len(requests[6]["messages"][-1]["content"]), 16_000)

	def test_embedding_suite_checks_single_batch_and_dimension_contract(self):
		requests: list[dict] = []

		class Embeddings:
			def create(self, *, model, input):
				requests.append({"model": model, "input": input})
				count = len(input) if isinstance(input, list) else 1
				return SimpleNamespace(
					data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in range(count)]
				)

		client = SimpleNamespace(embeddings=Embeddings())
		with patch(
			"frappe_ai.frappe_ai.doctype.ai_model.connection_test.create_openai_compatible_client",
			return_value=client,
		):
			result = run_capability_suite(
				{
					"model_type": "Embedding",
					"provider": "gemini",
					"model_id": "gemini/gemini-embedding-001",
				}
			)

		self.assertTrue(result["ok"])
		self.assertEqual(requests[0], {"model": "gemini-embedding-001", "input": ["frappe ai single embedding probe"]})
		self.assertEqual(requests[1]["model"], "gemini-embedding-001")
		self.assertEqual(requests[1]["input"], list(EMBEDDING_BATCH_INPUTS))


if __name__ == "__main__":
	unittest.main()
