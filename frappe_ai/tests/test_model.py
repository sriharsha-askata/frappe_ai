# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.lib.model import (
	LITELLM_PROVIDER_ALIASES,
	PROVIDER_ENDPOINT_DEFAULTS,
	create_openai_compatible_model,
	get_provider_endpoint,
	is_known_provider,
	normalize_provider_error,
	resolve_provider_credentials,
	to_litellm_provider,
)


class TestIsKnownProvider(IntegrationTestCase):
	def test_known_providers_accepted(self):
		for slug in ("openai", "anthropic", "ollama", "openrouter"):
			with self.subTest(slug=slug):
				self.assertTrue(is_known_provider(slug))

	def test_unknown_provider_rejected(self):
		self.assertFalse(is_known_provider("not-a-real-provider"))


class TestToLitellmProvider(IntegrationTestCase):
	def test_unaliased_slug_returned_unchanged(self):
		self.assertEqual(to_litellm_provider("openai"), "openai")

	def test_aliased_slugs_translated(self):
		import litellm

		known = {p.value for p in litellm.provider_list}
		for agno_slug, litellm_slug in LITELLM_PROVIDER_ALIASES.items():
			with self.subTest(slug=agno_slug):
				self.assertEqual(to_litellm_provider(agno_slug), litellm_slug)
				self.assertIn(litellm_slug, known)


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
		# Not "fireworks" — that is the app's stored slug spelling;
		# litellm's provider_list (which AIProvider._validate_provider_known now
		# validates against, ADR 0013) calls the same provider "fireworks_ai".
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
