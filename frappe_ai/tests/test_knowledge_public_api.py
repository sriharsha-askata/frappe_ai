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


class TestFindExistingCompleted(IntegrationTestCase):
	"""`find_existing_completed` resolves candidates' File rows in batches."""

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

	@staticmethod
	def _source_cls():
		return frappe.get_attr(
			"frappe_ai.frappe_ai.doctype.ai_knowledge_source.ai_knowledge_source.AIKnowledgeSource"
		)

	def _indexed(self, content: bytes | None = None):
		unique = frappe.generate_hash(length=8)
		file_doc = save_file(
			f"knowledge-dedup-{unique}.txt", content or f"dedup knowledge {unique}".encode(), None, None
		)
		kb = Knowledge(f"Dedup KB {unique}")
		with patch("frappe_ai.knowledge.embedder._call_openai_compatible", side_effect=self._fake_embed):
			source_name = kb.add_file(file_doc.file_url)
		return kb, file_doc, source_name

	def test_match_uses_stored_hash_without_reading_files(self):
		kb, file_doc, source_name = self._indexed()
		cls = self._source_cls()

		with patch.object(cls, "content_hash_for_file", side_effect=AssertionError("read bytes")):
			found = cls.find_existing_completed(kb.name, "File", file_doc.content_hash)

		self.assertEqual(found, source_name)

	def test_match_when_source_stores_the_file_name(self):
		kb, file_doc, source_name = self._indexed()
		frappe.db.set_value("AI Knowledge Source", source_name, "file", file_doc.name)

		found = self._source_cls().find_existing_completed(kb.name, "File", file_doc.content_hash)

		self.assertEqual(found, source_name)

	def test_legacy_file_without_stored_hash_matches_by_content(self):
		kb, file_doc, source_name = self._indexed()
		wanted = file_doc.content_hash
		frappe.db.set_value("File", file_doc.name, "content_hash", "")

		found = self._source_cls().find_existing_completed(kb.name, "File", wanted)

		self.assertEqual(found, source_name)

	def test_no_match_for_different_content(self):
		kb, _file_doc, _source_name = self._indexed()

		self.assertIsNone(self._source_cls().find_existing_completed(kb.name, "File", "0" * 32))

	def test_deleted_file_does_not_match(self):
		kb, file_doc, _source_name = self._indexed()
		wanted = file_doc.content_hash
		frappe.delete_doc("File", file_doc.name, force=True, ignore_permissions=True)

		self.assertIsNone(self._source_cls().find_existing_completed(kb.name, "File", wanted))

	def test_earliest_matching_source_wins(self):
		kb, file_doc, first = self._indexed()
		second = frappe.get_doc(
			{
				"doctype": "AI Knowledge Source",
				"title": "duplicate source",
				"knowledge_base": kb.name,
				"source_type": "File",
				"file": file_doc.file_url,
			}
		)
		second.flags.skip_auto_ingest = True
		second.insert(ignore_permissions=True)
		frappe.db.set_value("AI Knowledge Source", second.name, "status", "Completed")

		found = self._source_cls().find_existing_completed(kb.name, "File", file_doc.content_hash)

		self.assertEqual(found, first)

	def test_non_file_type_or_empty_hash_returns_none(self):
		kb, file_doc, _source_name = self._indexed()
		cls = self._source_cls()

		self.assertIsNone(cls.find_existing_completed(kb.name, "Text", file_doc.content_hash))
		self.assertIsNone(cls.find_existing_completed(kb.name, "File", ""))
