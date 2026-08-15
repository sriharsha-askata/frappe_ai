# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for the Frappe-side service endpoints (`frappe_ai/api/service.py`).

`get_service_config`'s shared-secret auth is the load-bearing case here: it is the
Frappe-side half of the FastAPI service's one real Phase 2 capability
(`frappe_client.get_service_config`), and unlike the rest of this app's whitelisted
methods it is `allow_guest=True` — the 401 in `_verify_service_secret` is what
actually gates access, so it needs direct coverage.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.api import service
from frappe_ai.api.service import _model_call_config, get_service_config
from frappe_ai.frappe_ai.doctype.ai_run.ai_run import create_run

TEST_SECRET = "test-service-secret-for-api-tests"


def _model_and_agent(title: str = "Service API Test Agent") -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc({"doctype": "AI Provider", "provider": "openai"}).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", "Service API Test Model"):
		frappe.get_doc(
			{"doctype": "AI Model", "title": "Service API Test Model", "provider": "openai", "model_id": "gpt-4o-mini"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Agent", title):
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": title,
				"model": "Service API Test Model",
				"instructions": "Be terse.",
			}
		).insert(ignore_permissions=True)
	return title


def _patch_request_header(header_value: str | None):
	"""Build a `frappe.get_request_header` stand-in for `X-Frappe-AI-Service-Secret`.

	Args:
		header_value (str | None): Value to return for the `X-Frappe-AI-Service-Secret`
			header.

	Returns:
		Callable: Drop-in replacement matching `frappe.get_request_header`'s signature.
	"""

	def _fake_get_request_header(key, default=None):
		if key == "X-Frappe-AI-Service-Secret":
			return header_value
		return default

	return _fake_get_request_header


class TestGetServiceConfigAuth(IntegrationTestCase):
	def setUp(self):
		# frappe_ai_service_secret lives in site_config.json (ADR 0011), not a
		# DocType field — patch frappe.conf directly rather than saving a doc.
		self._original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = TEST_SECRET

	def tearDown(self):
		if self._original_secret is None:
			frappe.conf.pop("frappe_ai_service_secret", None)
		else:
			frappe.conf.frappe_ai_service_secret = self._original_secret
		frappe.db.rollback()

	def test_valid_secret_succeeds(self):
		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = get_service_config()

		self.assertIn("request_timeout", result)
		self.assertIn("stream_timeout", result)
		self.assertIn("lancedb_path", result)

	def test_wrong_secret_rejected(self):
		with patch("frappe.get_request_header", new=_patch_request_header("wrong-secret")):
			with self.assertRaises(frappe.AuthenticationError):
				get_service_config()

	def test_missing_header_rejected(self):
		with patch("frappe.get_request_header", new=_patch_request_header(None)):
			with self.assertRaises(frappe.AuthenticationError):
				get_service_config()

	def test_empty_header_rejected(self):
		with patch("frappe.get_request_header", new=_patch_request_header("")):
			with self.assertRaises(frappe.AuthenticationError):
				get_service_config()


class TestModelCallConfig(IntegrationTestCase):
	"""`_model_call_config`'s two-state credential split (ADR 0013) — a linked
	Provider supplies the class + credentials outright; unlinked falls back to the
	model's own fields against the OpenAI-compatible Agno class. No merge either way."""

	def tearDown(self):
		frappe.db.rollback()

	def test_linked_provider_supplies_class_and_credentials(self):
		# "openrouter" — its Agno class needs no separate provider SDK install
		# (OpenAI-wire-compatible under the hood), unlike e.g. "groq".
		if not frappe.db.exists("AI Provider", "openrouter"):
			frappe.get_doc(
				{
					"doctype": "AI Provider",
					"provider": "openrouter",
					"api_key": "sk-from-provider",
					"base_url": "https://provider.example.com",
				}
			).insert()
		model_doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "Linked Config Model",
				"provider": "openrouter",
				"model_id": "some/model",
				# Deliberately different from the provider's — must be ignored when linked.
				"api_key": "sk-on-model",
				"base_url": "https://model.example.com",
			}
		).insert()

		config = _model_call_config(model_doc)

		self.assertEqual(config["class_name"], "OpenRouter")
		self.assertEqual(config["api_key"], "sk-from-provider")
		self.assertEqual(config["base_url"], "https://provider.example.com")

	def test_unlinked_model_uses_own_fields_and_openai_class(self):
		model_doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "Unlinked Config Model",
				"model_id": "llama-3.3-70b-versatile",
				"api_key": "sk-on-model",
				"base_url": "https://model.example.com",
			}
		).insert()

		config = _model_call_config(model_doc)

		self.assertEqual(config["class_name"], "OpenAIChat")
		self.assertEqual(config["api_key"], "sk-on-model")
		self.assertEqual(config["base_url"], "https://model.example.com")

	def test_unlinked_model_with_no_credentials_still_resolves(self):
		# No "No Provider" throw anymore (ADR 0013) — an unlinked model is runnable,
		# just with whatever (possibly empty) credentials it carries itself.
		model_doc = frappe.get_doc(
			{"doctype": "AI Model", "title": "Bare Unlinked Model", "model_id": "gpt-4o-mini"}
		).insert()

		config = _model_call_config(model_doc)

		self.assertEqual(config["class_name"], "OpenAIChat")
		self.assertIsNone(config["api_key"])
		self.assertFalse(config["base_url"])


class TestGetRunConfigAuth(IntegrationTestCase):
	def setUp(self):
		self._original_secret = frappe.conf.get("frappe_ai_service_secret")
		frappe.conf.frappe_ai_service_secret = TEST_SECRET

	def tearDown(self):
		if self._original_secret is None:
			frappe.conf.pop("frappe_ai_service_secret", None)
		else:
			frappe.conf.frappe_ai_service_secret = self._original_secret
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_guest_entry_uses_explicit_acting_user_for_run_lookup(self):
		agent = _model_and_agent("Service Run Config Agent")
		session = frappe.get_doc(
			{
				"doctype": "AI Session",
				"agent": agent,
				"source": "Trigger",
				"title": "Run Config Session",
			}
		).insert(ignore_permissions=True)
		run = create_run(
			source="Trigger",
			input="hello",
			session=session.name,
			config_snapshot={"auto_approve": False},
		)
		frappe.set_user("Guest")

		with patch("frappe.get_request_header", new=_patch_request_header(TEST_SECRET)):
			result = service.get_run_config(run.name, "Administrator")

		self.assertEqual(result["agent"]["name"], agent)
		self.assertEqual(json.loads(json.dumps(result["config_snapshot"])), {"auto_approve": False})
