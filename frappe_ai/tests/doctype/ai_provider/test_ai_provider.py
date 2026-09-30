# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt

from typing import Any

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.lib.model import resolve_provider_credentials


def _provider(**overrides: Any) -> dict:
	doc = {"doctype": "AI Provider", "provider": "openrouter", "api_key": "sk-provider", "enabled": 1}
	doc.update(overrides)
	return doc


def _model(**overrides: Any) -> dict:
	doc = {
		"doctype": "AI Model",
		"title": "Test Provider Model",
		"provider": "openrouter",
		"model_id": "test-model",
		"enabled": 1,
	}
	doc.update(overrides)
	return doc


class TestAIProviderValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_inserts_with_known_provider(self):
		doc = frappe.get_doc(_provider()).insert()
		self.assertEqual(doc.name, "openrouter")

	def test_provider_name_normalized_lowercase(self):
		doc = frappe.get_doc(_provider(provider="OpenRouter")).insert()
		self.assertEqual(doc.provider, "openrouter")

	def test_unknown_provider_rejected(self):
		doc = frappe.get_doc(_provider(provider="not-a-real-provider"))
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown provider"):
			doc.insert()

	def test_aliased_provider_slug_accepted(self):
		# "fireworks" is the app's stored spelling for a provider
		# litellm calls "fireworks_ai" — _validate_provider_known must translate via
		# LITELLM_PROVIDER_ALIASES before checking litellm.provider_list, so the
		# stored value stays "fireworks" (what the shared transport metadata uses).
		doc = frappe.get_doc(_provider(provider="fireworks")).insert()
		self.assertEqual(doc.provider, "fireworks")

	def test_invalid_base_url_rejected(self):
		doc = frappe.get_doc(_provider(base_url="not-a-url"))
		with self.assertRaisesRegex(frappe.ValidationError, "Base URL"):
			doc.insert()

	def test_extra_params_reserved_key_rejected(self):
		doc = frappe.get_doc(_provider(extra_params='{"api_key": "x"}'))
		with self.assertRaisesRegex(frappe.ValidationError, "reserved"):
			doc.insert()

	def test_extra_params_invalid_json_rejected(self):
		doc = frappe.get_doc(_provider(extra_params="{not json"))
		with self.assertRaisesRegex(frappe.ValidationError, "valid JSON"):
			doc.insert()


class TestProviderCredentialResolution(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_model_resolves_key_from_provider(self):
		frappe.get_doc(_provider(api_key="sk-from-provider")).insert()
		frappe.get_doc(_model()).insert()
		creds = resolve_provider_credentials("openrouter")
		self.assertEqual(creds["api_key"], "sk-from-provider")

	def test_model_own_key_ignored_when_provider_linked(self):
		# resolve_provider_credentials itself just resolves the AI Provider row — it
		# never looks at the model at all. Confirms a model's own api_key has no
		# bearing on what this function returns regardless of what the model sets;
		# _model_call_config (api/service.py) never even reads a linked model's own
		# api_key/base_url per ADR 0013's two-state split.
		frappe.get_doc(_provider(api_key="sk-from-provider")).insert()
		frappe.get_doc(_model(api_key="sk-on-model")).insert()
		creds = resolve_provider_credentials("openrouter")
		self.assertEqual(creds["api_key"], "sk-from-provider")

	def test_disabled_provider_not_used(self):
		frappe.get_doc(_provider(api_key="sk-from-provider", enabled=0)).insert()
		creds = resolve_provider_credentials("openrouter")
		self.assertEqual(creds, {})

	def test_no_provider_row_returns_empty(self):
		self.assertEqual(resolve_provider_credentials("openrouter"), {})

	def test_provider_base_url_resolved(self):
		frappe.get_doc(_provider(base_url="http://gateway.local")).insert()
		creds = resolve_provider_credentials("openrouter")
		self.assertEqual(creds["base_url"], "http://gateway.local")

	def test_provider_extra_params_resolved(self):
		frappe.get_doc(_provider(extra_params='{"api_version": "v1", "temperature": 0.1}')).insert()
		creds = resolve_provider_credentials("openrouter")
		self.assertEqual(creds["extra_params"], {"api_version": "v1", "temperature": 0.1})
