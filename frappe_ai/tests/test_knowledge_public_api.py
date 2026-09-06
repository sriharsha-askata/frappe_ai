# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Integration coverage for the app-agnostic knowledge loading surface."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.file_manager import save_file

from frappe_ai.knowledge import Knowledge, store


class TestKnowledgePublicAPI(IntegrationTestCase):
	def setUp(self):
		store.drop_table()
		frappe.db.set_single_value("AI Settings", "embedding_dimension", 4)
		frappe.db.set_single_value("AI Settings", "chunk_size", 200)
		frappe.db.set_single_value("AI Settings", "chunk_overlap", 20)
		frappe.clear_document_cache("AI Settings", "AI Settings")

	def tearDown(self):
		store.drop_table()
		frappe.db.rollback()

	@staticmethod
	def _fake_embed(texts, config, *, timeout):
		return [(index, [0.1, 0.2, 0.3, 0.4]) for index in range(len(texts))]

	def test_add_files_preserves_input_order_and_deduplicates(self):
		first = save_file("knowledge-public-api-a.txt", b"same knowledge content", None, None)
		second = save_file("knowledge-public-api-b.txt", b"same knowledge content", None, None)
		kb = Knowledge("Public API KB")

		with patch("frappe_ai.knowledge.embedder._call_openai_compatible", side_effect=self._fake_embed):
			result = kb.add_files([first.file_url, second.file_url])

		self.assertEqual(len(result), 2)
		self.assertEqual(result[0], result[1])
		self.assertEqual(frappe.db.count("AI Knowledge Source", {"knowledge_base": kb.name}), 1)

	def test_load_files_returns_knowledge_handle(self):
		file_doc = save_file("knowledge-public-api-load.txt", b"loadable knowledge", None, None)

		with patch("frappe_ai.knowledge.embedder._call_openai_compatible", side_effect=self._fake_embed):
			knowledge = Knowledge.load_files([file_doc.file_url], kb_title="Loaded API KB")

		self.assertIsInstance(knowledge, Knowledge)
		self.assertEqual(knowledge.name, "Loaded API KB")
		self.assertEqual(
			frappe.db.count("AI Knowledge Source", {"knowledge_base": knowledge.name}),
			1,
		)

	def test_successfully_indexed_source_is_marked_embedded(self):
		file_doc = save_file("knowledge-public-api-embedded.txt", b"embedded knowledge", None, None)
		kb = Knowledge("Embedded API KB")

		with patch("frappe_ai.knowledge.embedder._call_openai_compatible", side_effect=self._fake_embed):
			source_name = kb.add_file(file_doc.file_url)

		source = frappe.get_doc("AI Knowledge Source", source_name)
		self.assertEqual(source.is_embedded, 1)

	def test_visibility_endpoints_report_file_and_inventory(self):
		unique = frappe.generate_hash(length=8)
		file_doc = save_file(
			"knowledge-public-api-status-{}.txt".format(unique),
			("status knowledge " + unique).encode(),
			None,
			None,
		)
		kb = Knowledge("Visibility API KB {}".format(unique))

		with patch("frappe_ai.knowledge.embedder._call_openai_compatible", side_effect=self._fake_embed):
			source_name = kb.add_file(file_doc.file_url)

		status = frappe.get_attr(
			"frappe_ai.frappe_ai.doctype.ai_knowledge_source.ai_knowledge_source.AIKnowledgeSource"
		).get_file_status(file_doc.file_url)
		inventory = frappe.get_attr(
			"frappe_ai.frappe_ai.doctype.ai_knowledge_source.ai_knowledge_source.AIKnowledgeSource"
		).get_knowledge_base_inventory(kb.name)

		self.assertTrue(status["content_hash"])
		self.assertEqual(status["sources"][0]["name"], source_name)
		self.assertEqual(status["sources"][0]["is_embedded"], 1)
		self.assertTrue(inventory["exists"])
		self.assertEqual(inventory["totals"]["source_count"], 1)
		self.assertEqual(inventory["totals"]["by_status"]["Completed"], 1)
		self.assertEqual(inventory["sources"][0]["is_embedded"], 1)
