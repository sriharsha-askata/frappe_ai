# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.frappe_ai.doctype.ai_model.ai_model import get_provider_models
from frappe_ai.lib.model import (
	PROVIDER_ENDPOINT_DEFAULTS,
	create_openai_compatible_model,
	get_provider_endpoint,
	is_valid_provider_slug,
	list_model_ids,
	normalize_provider_error,
	resolve_provider_credentials,
)


class TestProviderSlug(IntegrationTestCase):
	def test_well_formed_slugs_are_accepted(self):
		for slug in ("openai", "anthropic", "ollama", "openrouter", "my-gateway", "azure.eu_1", " OpenAI "):
			with self.subTest(slug=slug):
				self.assertTrue(is_valid_provider_slug(slug))

	def test_malformed_slugs_are_rejected(self):
		for slug in ("", None, "has space", "-leading", "semi;colon", "slash/slug", "a" * 0):
			with self.subTest(slug=slug):
				self.assertFalse(is_valid_provider_slug(slug))

	def test_provider_doctype_rejects_a_malformed_slug(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Invalid provider"):
			frappe.get_doc({"doctype": "AI Provider", "provider": "bad slug!", "enabled": 1}).insert()

	def test_provider_without_a_known_endpoint_is_still_accepted(self):
		doc = frappe.get_doc({"doctype": "AI Provider", "provider": "my-gateway", "base_url": "http://gw.local/v1", "enabled": 1}).insert()
		self.assertEqual(doc.provider, "my-gateway")

	def test_anthropic_has_an_endpoint_default(self):
		self.assertEqual(get_provider_endpoint("anthropic"), "https://api.anthropic.com/v1/")


class TestListModelIds(IntegrationTestCase):
	def test_ids_are_sorted_and_unique(self):
		models = [type("M", (), {"id": i})() for i in ("gpt-b", "gpt-a", "gpt-b")]
		client = type("C", (), {"models": type("Ms", (), {"list": staticmethod(lambda: models)})()})()
		with patch("frappe_ai.lib.model.create_openai_compatible_client", return_value=client) as make:
			self.assertEqual(list_model_ids({"api_key": "k", "base_url": "http://x/v1"}), ["gpt-a", "gpt-b"])
		self.assertEqual(make.call_args.kwargs["max_retries"], 0)


class TestGetProviderModels(IntegrationTestCase):
	def setUp(self):
		frappe.cache.delete_value("frappe_ai:provider_models:suggest-gw")
		frappe.get_doc({"doctype": "AI Provider", "provider": "suggest-gw", "base_url": "http://gw.local/v1", "api_key": "k", "enabled": 1}).insert()

	def tearDown(self):
		frappe.cache.delete_value("frappe_ai:provider_models:suggest-gw")
		frappe.db.rollback()

	def test_suggestions_come_from_the_provider_and_skip_embedding_models(self):
		ids = ["chat-large", "text-embedding-3-small", "chat-small", "nomic-embed-text"]
		with patch("frappe_ai.frappe_ai.doctype.ai_model.ai_model.list_model_ids", return_value=ids) as fetch:
			self.assertEqual(get_provider_models("suggest-gw"), ["chat-large", "chat-small"])
		self.assertEqual(fetch.call_args.args[0]["base_url"], "http://gw.local/v1")

	def test_results_are_cached(self):
		with patch("frappe_ai.frappe_ai.doctype.ai_model.ai_model.list_model_ids", return_value=["a"]) as fetch:
			get_provider_models("suggest-gw")
			get_provider_models("suggest-gw")
		self.assertEqual(fetch.call_count, 1)

	def test_an_unreachable_provider_gives_no_suggestions(self):
		with patch("frappe_ai.frappe_ai.doctype.ai_model.ai_model.list_model_ids", side_effect=ConnectionError("down")):
			self.assertEqual(get_provider_models("suggest-gw"), [])

	def test_unknown_or_blank_provider_gives_no_suggestions(self):
		self.assertEqual(get_provider_models(None), [])
		self.assertEqual(get_provider_models("does-not-exist"), [])


class TestOpenAICompatibleTransport(IntegrationTestCase):
	def test_all_providers_use_one_openai_transport(self):
		for provider in ("openai", "google", "groq"):
			with self.subTest(provider=provider):
				model = create_openai_compatible_model(
					{
						"provider": provider,
						"model_id": "test-model",
						"api_key": "test-key",
						"base_url": get_provider_endpoint(provider),
						"params": {},
					}
				)
				self.assertEqual(model.__class__.__name__, "OpenAIChat")
				self.assertEqual(model.provider, provider)

	def test_google_transport_does_not_require_native_google_sdk(self):
		# Creating the transport must not import google.genai. The compatibility
		# endpoint is passed to the OpenAI SDK instead.
		import sys

		self.assertNotIn("google.genai", sys.modules)
		model = create_openai_compatible_model(
			{
				"provider": "google",
				"model_id": "gemini-2.5-flash",
				"api_key": "gemini-key",
				"base_url": get_provider_endpoint("google"),
				"params": {"extra_body": {"thinking_config": {"thinking_budget": 0}}},
			}
		)
		self.assertEqual(model.base_url, PROVIDER_ENDPOINT_DEFAULTS["google"])
		self.assertEqual(model.extra_body["thinking_config"]["thinking_budget"], 0)

	def test_retries_are_bounded(self):
		model = create_openai_compatible_model(
			{"provider": "openai", "model_id": "test-model", "params": {"max_retries": 99}}
		)
		self.assertEqual(model.max_retries, 2)


class TestResolveProviderCredentials(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_returns_empty_for_missing_provider(self):
		self.assertEqual(resolve_provider_credentials("not-a-real-provider"), {})

	def test_returns_empty_for_disabled_provider(self):
		frappe.get_doc({"doctype": "AI Provider", "provider": "cohere", "enabled": 0}).insert()
		self.assertEqual(resolve_provider_credentials("cohere"), {})

	def test_returns_credentials_for_enabled_provider(self):
		frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider": "mistral",
				"api_key": "sk-test",
				"base_url": "http://gateway.local",
				"enabled": 1,
			}
		).insert()
		creds = resolve_provider_credentials("mistral")
		self.assertEqual(creds["api_key"], "sk-test")
		self.assertEqual(creds["base_url"], "http://gateway.local")

	def test_extra_params_parsed(self):
		# A distinct slug from the others in this file — "groq"/"mistral"/"cohere" are
		# real-world slugs someone may have already configured with real credentials
		# (e.g. for live-testing) outside this test's rollback-scoped transaction.
		frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider": "cerebras",
				"extra_params": '{"api_version": "v1"}',
				"enabled": 1,
			}
		).insert()
		creds = resolve_provider_credentials("cerebras")
		self.assertEqual(creds["extra_params"], {"api_version": "v1"})

	def test_provider_endpoint_default_is_used(self):
		self.assertEqual(get_provider_endpoint("google"), PROVIDER_ENDPOINT_DEFAULTS["google"])
		self.assertEqual(get_provider_endpoint("google", "https://proxy.example/v1"), "https://proxy.example/v1")


class TestNormalizedProviderError(IntegrationTestCase):
	def test_rate_limit_is_retryable(self):
		error = type("RateLimit", (), {"status_code": 429, "message": "too many requests"})()
		result = normalize_provider_error(error, provider="google", model_id="gemini-2.5-flash")
		self.assertEqual(result.code, "rate_limit")
		self.assertTrue(result.retryable)

	def test_invalid_model_is_not_retryable(self):
		error = type("NotFound", (), {"status_code": 404, "message": "model not found"})()
		result = normalize_provider_error(error, provider="google", model_id="bad-model")
		self.assertEqual(result.code, "invalid_model")
		self.assertFalse(result.retryable)
