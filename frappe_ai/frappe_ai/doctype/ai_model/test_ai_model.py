# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt

from typing import Any

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
		self.assertTrue(doc.enabled)

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
		# "fireworks" is Agno's/PROVIDER_MODEL_CLASSES's own spelling; litellm's
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
