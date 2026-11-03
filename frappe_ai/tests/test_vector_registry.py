# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Choosing the active vector backend for a site."""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.vector import registry
from frappe_ai.vector.base import BackendUnavailable, VectorBackend


def _set_backend(value: str | None) -> None:
	frappe.db.set_single_value("AI Settings", "vector_backend", value or "")
	frappe.clear_document_cache("AI Settings", "AI Settings")


class TestVectorRegistry(IntegrationTestCase):
	def setUp(self):
		registry.reset_cache()

	def tearDown(self):
		registry.reset_cache()
		frappe.db.rollback()

	def test_auto_uses_lancedb_on_mariadb(self):
		_set_backend("Auto")
		with patch.object(frappe.db, "db_type", "mariadb"):
			self.assertEqual(registry.configured_name(), "lancedb")

	def test_auto_uses_pgvector_on_postgres(self):
		_set_backend("Auto")
		with patch.object(frappe.db, "db_type", "postgres"):
			self.assertEqual(registry.configured_name(), "pgvector")

	def test_blank_setting_means_auto(self):
		_set_backend("")
		with patch.object(frappe.db, "db_type", "mariadb"):
			self.assertEqual(registry.configured_name(), "lancedb")

	def test_explicit_choice_is_case_insensitive(self):
		_set_backend("Chroma")
		self.assertEqual(registry.configured_name(), "chroma")

	def test_unknown_backend_is_rejected(self):
		_set_backend("nonsense")
		with self.assertRaises(BackendUnavailable):
			registry.configured_name()

	def test_pgvector_cannot_be_built_on_mariadb(self):
		_set_backend("pgvector")
		with patch.object(frappe.db, "db_type", "mariadb"):
			with self.assertRaises(BackendUnavailable):
				registry.get_backend()

	def test_lancedb_backend_is_built_and_cached(self):
		_set_backend("lancedb")
		first = registry.get_backend()
		self.assertEqual(first.name, "lancedb")
		self.assertIs(registry.get_backend(), first)
		registry.reset_cache()
		self.assertIsNot(registry.get_backend(), first)

	def test_app_registered_backend_can_be_selected(self):
		class Dummy(VectorBackend):
			name = "dummy"

			def ensure_collection(self, *args, **kwargs): ...
			def info(self, collection): ...
			def upsert(self, collection, items): ...
			def delete(self, collection, *, ids=None, where=None): ...
			def search(self, collection, **kwargs): ...
			def list_ids(self, collection): ...
			def drop(self, collection): ...

		_set_backend("dummy")
		hooks = {"dummy": ["frappe_ai.tests.test_vector_registry.make_dummy"]}
		with patch("frappe.get_hooks", return_value=hooks):
			self.assertIn("dummy", registry.available_backends())
			self.assertEqual(registry.configured_name(), "dummy")
			with patch("frappe.get_attr", return_value=Dummy):
				self.assertEqual(registry.get_backend().name, "dummy")
