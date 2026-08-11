# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.lib.model import (
	LITELLM_PROVIDER_ALIASES,
	PROVIDER_MODEL_CLASSES,
	get_model_class,
	is_known_provider,
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


class TestGetModelClass(IntegrationTestCase):
	def test_resolves_openai_class(self):
		# openai's own SDK is a transitive dependency already present in this bench,
		# so this import succeeds without any provider-specific SDK install.
		cls = get_model_class("openai")
		self.assertEqual(cls.__name__, "OpenAIChat")

	def test_unknown_provider_key_error(self):
		with self.assertRaises(KeyError):
			get_model_class("not-a-real-provider")

	def test_every_mapped_provider_has_a_resolvable_module(self):
		# Module existence only — not import — so this doesn't depend on optional
		# per-provider SDKs (anthropic, google-genai, ...) being installed.
		import importlib.util

		for slug, (module_path, _class_name) in PROVIDER_MODEL_CLASSES.items():
			with self.subTest(slug=slug):
				self.assertIsNotNone(importlib.util.find_spec(module_path))


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
		# Not "fireworks" — that's Agno's/PROVIDER_MODEL_CLASSES's own slug spelling;
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
