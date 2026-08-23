# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase


def _model(**overrides: Any) -> dict:
	doc = {
		"doctype": "AI Model",
		"title": "Test Model",
		"model_id": "gpt-4o-mini",
		"enabled": 1,
	}
	doc.update(overrides)
	return doc


class TestAIModelValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_valid_model_inserts(self):
		doc = frappe.get_doc(_model()).insert()

		self.assertEqual(doc.model_id, "gpt-4o-mini")
		self.assertEqual(doc.model_type, "Chat")
		self.assertTrue(doc.enabled)

	def test_embedding_model_type_is_explicit(self):
		doc = frappe.get_doc(_model(model_type="Embedding", model_id="text-embedding-3-small")).insert()

		self.assertEqual(doc.model_type, "Embedding")

	def test_normalize_strips_whitespace(self):
		doc = frappe.get_doc(
			_model(title="  Padded  ", model_id="  gpt-4o-mini  ", base_url="  https://api.example.com  ")
		).insert()

		self.assertEqual(doc.title, "Padded")
		self.assertEqual(doc.model_id, "gpt-4o-mini")
		self.assertEqual(doc.base_url, "https://api.example.com")

	def test_slash_prefixed_model_id_still_accepted_as_bare_id(self):
		# model_id is bare — no provider/ prefix is required, but the pattern still
		# tolerates internal slashes (e.g. namespaced ids some providers use).
		doc = frappe.get_doc(_model(model_id="meta-llama/llama-3.1-8b")).insert()
		self.assertEqual(doc.model_id, "meta-llama/llama-3.1-8b")

	def test_invalid_model_id_format_rejected(self):
		for bad in ("", " ", "has space", "!bad"):
			with self.subTest(model_id=bad):
				doc = frappe.get_doc(_model(model_id=bad))
				with self.assertRaisesRegex(frappe.ValidationError, "Model ID"):
					doc.insert()

	def test_unknown_provider_rejected(self):
		# `provider` is a real Link -> AI Provider again (ADR 0013) — Frappe core's
		# own _validate_links rejects a docname with no matching row.
		doc = frappe.get_doc(_model(provider="not-a-real-provider-doc"))

		with self.assertRaises(frappe.LinkValidationError):
			doc.insert()


class TestAIModelProvider(IntegrationTestCase):
	def setUp(self):
		if not frappe.db.exists("AI Provider", "anthropic"):
			frappe.get_doc({"doctype": "AI Provider", "provider": "anthropic"}).insert()

	def tearDown(self):
		frappe.db.rollback()

	def test_linked_provider_does_not_alter_model_id(self):
		# Unlike flow, the provider is not composed into model_id — it stays bare.
		doc = frappe.get_doc(_model(provider="anthropic", model_id="claude-sonnet-4-6")).insert()

		self.assertEqual(doc.model_id, "claude-sonnet-4-6")

	def test_resave_does_not_change_model_id(self):
		doc = frappe.get_doc(_model(provider="anthropic", model_id="claude-sonnet-4-6")).insert()
		doc.save()

		self.assertEqual(doc.model_id, "claude-sonnet-4-6")

	def test_model_without_provider_still_works(self):
		doc = frappe.get_doc(_model(model_id="gpt-4o-mini")).insert()

		self.assertFalse(doc.provider)
		self.assertEqual(doc.model_id, "gpt-4o-mini")

	def test_model_with_real_provider_saves(self):
		doc = frappe.get_doc(_model(provider="anthropic", model_id="claude-sonnet-4-6")).insert()

		self.assertEqual(doc.provider, "anthropic")


class TestAIModelBaseURL(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_https_base_url_accepted(self):
		doc = frappe.get_doc(_model(base_url="https://api.example.com/v1")).insert()

		self.assertEqual(doc.base_url, "https://api.example.com/v1")

	def test_http_base_url_accepted(self):
		doc = frappe.get_doc(_model(base_url="http://localhost:11434")).insert()

		self.assertEqual(doc.base_url, "http://localhost:11434")

	def test_non_http_base_url_rejected(self):
		for bad in ("ftp://example.com", "not-a-url", "example.com", "//example.com"):
			with self.subTest(base_url=bad):
				doc = frappe.get_doc(_model(base_url=bad))
				with self.assertRaisesRegex(frappe.ValidationError, "Base URL"):
					doc.insert()


class TestAIModelParams(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_valid_params_json_accepted(self):
		doc = frappe.get_doc(_model(params='{"temperature": 0.2, "max_tokens": 500}')).insert()

		import json

		self.assertEqual(json.loads(doc.params), {"temperature": 0.2, "max_tokens": 500})

	def test_invalid_json_rejected(self):
		doc = frappe.get_doc(_model(params="{not json"))

		with self.assertRaisesRegex(frappe.ValidationError, "Params"):
			doc.insert()

	def test_non_object_json_rejected(self):
		doc = frappe.get_doc(_model(params="[1, 2, 3]"))

		with self.assertRaisesRegex(frappe.ValidationError, "Params"):
			doc.insert()

	def test_reserved_params_rejected(self):
		for reserved in ("model", "api_key", "messages", "stream", "tools"):
			with self.subTest(key=reserved):
				doc = frappe.get_doc(_model(params=f'{{"{reserved}": "x"}}'))
				with self.assertRaisesRegex(frappe.ValidationError, "reserved keys"):
					doc.insert()


class TestAIModelDefault(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_is_default_clears_other_defaults(self):
		first = frappe.get_doc(_model(title="First Model", is_default=1)).insert()
		self.assertTrue(first.is_default)

		second = frappe.get_doc(_model(title="Second Model", is_default=1)).insert()
		self.assertTrue(second.is_default)

		# The first model must have had is_default cleared.
		self.assertEqual(frappe.db.get_value("AI Model", first.name, "is_default"), 0)

	def test_only_one_default_at_a_time(self):
		frappe.get_doc(_model(title="Alpha Model", is_default=1)).insert()
		frappe.get_doc(_model(title="Beta Model", is_default=1)).insert()

		defaults = frappe.db.count("AI Model", {"is_default": 1})
		self.assertEqual(defaults, 1)

	def test_get_default_model_returns_none_when_none_set(self):
		from frappe_ai.lib.model import get_default_model

		frappe.get_doc(_model(title="No Default Model", is_default=0)).insert()
		self.assertIsNone(get_default_model())

	def test_get_default_model_returns_enabled_default(self):
		from frappe_ai.lib.model import get_default_model

		doc = frappe.get_doc(_model(title="Default Model", is_default=1, enabled=1)).insert()
		self.assertEqual(get_default_model(), doc.name)

	def test_get_default_model_ignores_disabled(self):
		from frappe_ai.lib.model import get_default_model

		frappe.get_doc(_model(title="Disabled Default", is_default=1, enabled=0)).insert()
		self.assertIsNone(get_default_model())


class TestGetProviderModels(IntegrationTestCase):
	def test_known_agno_provider_returns_suggestions(self):
		from frappe_ai.frappe_ai.doctype.ai_model.ai_model import get_provider_models

		models = get_provider_models("openai")
		self.assertIsInstance(models, list)
		self.assertTrue(models)
		self.assertEqual(models, sorted(models))

	def test_provider_agno_cannot_run_returns_empty(self):
		from frappe_ai.frappe_ai.doctype.ai_model.ai_model import get_provider_models

		# litellm knows many providers frappe_ai/Agno has no model class for.
		self.assertEqual(get_provider_models("not-a-known-agno-provider"), [])

	def test_empty_provider_returns_empty(self):
		from frappe_ai.frappe_ai.doctype.ai_model.ai_model import get_provider_models

		self.assertEqual(get_provider_models(None), [])
		self.assertEqual(get_provider_models(""), [])

	def test_aliased_provider_slug_returns_suggestions(self):
			# "fireworks" is the app's stored spelling; litellm's
		# models_by_provider keys on "fireworks_ai" instead (LITELLM_PROVIDER_ALIASES).
		from frappe_ai.frappe_ai.doctype.ai_model.ai_model import get_provider_models

		models = get_provider_models("fireworks")
		self.assertTrue(models)


class TestContextWindow(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_context_window_is_user_editable(self):
		# No auto-detection (ADR 0009) — the value the user sets is what's persisted.
		doc = frappe.get_doc(_model(context_window=128000)).insert()
		self.assertEqual(doc.context_window, 128000)

	def test_context_window_defaults_to_zero(self):
		doc = frappe.get_doc(_model()).insert()
		# Int fields are None on the in-memory doc until the DB default is applied on
		# reload; reload to check the persisted value rather than the pre-reload object.
		reloaded = frappe.get_doc("AI Model", doc.name)
		self.assertEqual(reloaded.context_window, 0)

	def test_context_window_survives_model_id_change(self):
		# Unlike flow, changing model_id does not trigger any re-detection — the
		# user-set value is left untouched.
		doc = frappe.get_doc(_model(context_window=128000)).insert()
		doc.model_id = "claude-sonnet-4-6"
		doc.save()
		self.assertEqual(doc.context_window, 128000)


class TestAIModelConnection(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _connection_model(self):
		return frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "Connection Transport Model",
				"model_id": "gemini-2.5-flash",
				"api_key": "test-key",
				"base_url": "https://provider.example/v1",
			}
		).insert()

	def _embedding_connection_model(self):
		if not frappe.db.exists("AI Provider", "gemini"):
			frappe.get_doc({"doctype": "AI Provider", "provider": "gemini"}).insert()
		frappe.db.set_value("AI Provider", "gemini", "enabled", 1)
		return frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "Embedding Connection Model",
				"provider": "gemini",
				"model_id": "gemini/gemini-embedding-001",
				"model_type": "Embedding",
				"api_key": "test-key",
				"base_url": "https://provider.example/v1",
			}
		).insert()

	def test_connection_uses_shared_transport(self):
		doc = self._connection_model()
		fake_model = _FakeChatModel()

		with patch(
			"frappe_ai.frappe_ai.doctype.ai_model.connection_test.create_openai_compatible_model",
			return_value=fake_model,
		) as factory:
			result = doc.test_connection()

		self.assertTrue(result["ok"])
		self.assertEqual(
			[check["name"] for check in result["checks"]],
			["chat", "streaming", "tool_declaration", "tool_call", "structured_output", "large_input"],
		)
		factory.assert_called_once()
		self.assertEqual(factory.call_args.kwargs["timeout"], 15)
		self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
		self.assertEqual(len(fake_model.requests), 6)
		self.assertEqual(fake_model.synthetic_invocations, 1)

		# Explicit invocations never reuse a prior result or model instance.
		with patch(
			"frappe_ai.frappe_ai.doctype.ai_model.connection_test.create_openai_compatible_model",
			return_value=_FakeChatModel(),
		) as second_factory:
			second = doc.test_connection()
		self.assertTrue(second["ok"])
		second_factory.assert_called_once()

	def test_embedding_connection_uses_embeddings_endpoint_and_normalizes_gemini_id(self):
		doc = self._embedding_connection_model()
		client = _FakeEmbeddingClient()

		with patch(
			"frappe_ai.frappe_ai.doctype.ai_model.connection_test.create_openai_compatible_client",
			return_value=client,
		) as factory:
			result = doc.test_connection()

		self.assertTrue(result["ok"])
		self.assertEqual([check["name"] for check in result["checks"]], ["embedding_single", "embedding_batch", "embedding_dimensions"])
		factory.assert_called_once()
		self.assertEqual(factory.call_args.kwargs["timeout"], 15)
		self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
		self.assertEqual(client.requests[0], {"model": "gemini-embedding-001", "input": ["frappe ai single embedding probe"]})
		self.assertEqual(client.requests[1], {"model": "gemini-embedding-001", "input": ["frappe ai embedding probe one", "frappe ai embedding probe two"]})

	def test_connection_returns_normalized_failure_and_blocks_dependents(self):
		doc = self._connection_model()
		error = type("RateLimit", (Exception,), {"status_code": 429, "message": "too many requests"})()
		fake_model = _FakeChatModel(error=error)

		with patch(
			"frappe_ai.frappe_ai.doctype.ai_model.connection_test.create_openai_compatible_model",
			return_value=fake_model,
		):
			result = doc.test_connection()

		self.assertFalse(result["ok"])
		checks = {check["name"]: check for check in result["checks"]}
		self.assertEqual(checks["chat"]["status"], "failed")
		self.assertEqual(checks["chat"]["code"], "rate_limit")
		for name in ("streaming", "tool_declaration", "tool_call", "structured_output", "large_input"):
			self.assertEqual(checks[name]["status"], "blocked")

	def test_optional_capability_failures_are_warnings(self):
		doc = self._connection_model()
		fake_model = _FakeChatModel(
			structured_error=RuntimeError("response_format is not supported"),
			large_error=RuntimeError("context length exceeded"),
		)

		with patch(
			"frappe_ai.frappe_ai.doctype.ai_model.connection_test.create_openai_compatible_model",
			return_value=fake_model,
		):
			result = doc.test_connection()

		self.assertTrue(result["ok"])
		checks = {check["name"]: check for check in result["checks"]}
		self.assertEqual(checks["structured_output"]["status"], "warning")
		self.assertEqual(checks["large_input"]["status"], "warning")
		self.assertEqual(len(result["warnings"]), 2)


class _FakeChatModel:
	def __init__(self, error=None, structured_error=None, large_error=None):
		self.error = error
		self.structured_error = structured_error
		self.large_error = large_error
		self.requests = []
		self.synthetic_invocations = 0

	def response(self, *, messages, response_format=None, tools=None, tool_choice=None):
		self.requests.append({"messages": messages, "response_format": response_format, "tools": tools, "tool_choice": tool_choice})
		if self.error:
			raise self.error
		if response_format and self.structured_error:
			raise self.structured_error
		if self.large_error and any(len(str(getattr(message, "content", ""))) > 1000 for message in messages):
			raise self.large_error
		if tools and tool_choice != "none":
			self.synthetic_invocations += 1
			tools[0].entrypoint()
		return SimpleNamespace(content='{"answer":"OK"}' if response_format else "OK")

	def response_stream(self, *, messages):
		self.requests.append({"messages": messages, "stream": True})
		if self.error:
			raise self.error
		return iter([SimpleNamespace(content="OK")])


class _FakeEmbeddingClient:
	def __init__(self):
		self.requests = []
		self.embeddings = self

	def create(self, *, model, input):
		self.requests.append({"model": model, "input": input})
		count = len(input) if isinstance(input, list) else 1
		return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in range(count)])
