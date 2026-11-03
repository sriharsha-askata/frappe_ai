# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Contract tests for vector backends. They need no Frappe site.

Every backend must pass the same suite. Backends whose dependency or service is missing are
skipped: LanceDB and Chroma need their packages, pgvector needs `psycopg` and a database
with the extension available, given by `FRAPPE_AI_TEST_PG_DSN`.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid

from frappe_ai.vector.base import (
	BackendUnavailable,
	DimensionMismatch,
	IdentityMismatch,
	Item,
	VectorStoreNotReady,
	check_identifier,
	rrf_fuse,
)

IDENTITY = {"provider": "test", "model": "unit", "dimension": 4}
OTHER_IDENTITY = {"provider": "test", "model": "other", "dimension": 4}
FILTERS = ["kb", "source"]


def _items() -> list[Item]:
	return [
		Item("1", "printer setup guide for the office", [1.0, 0.0, 0.0, 0.0], {"kb": "A", "source": "s1"}),
		Item("2", "laptop onboarding checklist", [0.0, 1.0, 0.0, 0.0], {"kb": "A", "source": "s2"}),
		Item("3", "printer toner replacement", [0.9, 0.1, 0.0, 0.0], {"kb": "B", "source": "s3"}),
		Item("4", "holiday calendar", [0.0, 0.0, 1.0, 0.0], {"kb": "B", "source": "s4"}),
	]


class BackendContract:
	"""Mixin: subclasses provide `make_backend()` and the `keyword` capability flag."""

	keyword = True

	def make_backend(self):  # pragma: no cover - supplied by subclasses
		raise NotImplementedError

	def setUp(self):
		self.backend = self.make_backend()
		self.name = "c" + uuid.uuid4().hex[:10]
		self.addCleanup(self.backend.drop, self.name)

	def ensure(self, **kw):
		args = dict(dimension=4, identity=IDENTITY, filterable=FILTERS)
		args.update(kw)
		self.backend.ensure_collection(self.name, **args)

	def filled(self):
		self.ensure()
		self.backend.upsert(self.name, _items())

	def ids(self, hits):
		return [h.id for h in hits]

	# -- lifecycle ---------------------------------------------------------

	def test_info_of_missing_collection_is_none(self):
		self.assertIsNone(self.backend.info(self.name))

	def test_ensure_creates_and_is_idempotent(self):
		self.ensure()
		self.ensure()
		info = self.backend.info(self.name)
		self.assertEqual(info.dimension, 4)
		self.assertEqual(dict(info.identity), IDENTITY)
		self.assertEqual(info.count, 0)

	def test_dimension_mismatch_raises(self):
		self.ensure()
		with self.assertRaises(DimensionMismatch):
			self.ensure(dimension=5, identity={**IDENTITY, "dimension": 5})

	def test_identity_mismatch_raises(self):
		self.ensure()
		with self.assertRaises(IdentityMismatch):
			self.ensure(identity=OTHER_IDENTITY)

	def test_recreate_on_mismatch_replaces_the_collection(self):
		self.filled()
		self.ensure(identity=OTHER_IDENTITY, recreate_on_mismatch=True)
		info = self.backend.info(self.name)
		self.assertEqual(info.count, 0)
		self.assertEqual(dict(info.identity), OTHER_IDENTITY)

	def test_invalid_dimension_rejected(self):
		with self.assertRaises(ValueError):
			self.ensure(dimension=0)

	def test_list_ids_tracks_writes_and_deletes(self):
		self.assertEqual(self.backend.list_ids(self.name), set())
		self.filled()
		self.assertEqual(self.backend.list_ids(self.name), {"1", "2", "3", "4"})
		self.backend.delete(self.name, ids=["2"])
		self.assertEqual(self.backend.list_ids(self.name), {"1", "3", "4"})

	def test_drop_removes_and_is_idempotent(self):
		self.filled()
		self.backend.drop(self.name)
		self.backend.drop(self.name)
		self.assertIsNone(self.backend.info(self.name))

	# -- writes ------------------------------------------------------------

	def test_upsert_requires_existing_collection(self):
		with self.assertRaises(VectorStoreNotReady):
			self.backend.upsert(self.name, _items())

	def test_upsert_replaces_by_id(self):
		self.filled()
		self.backend.upsert(
			self.name, [Item("1", "replaced", [0.0, 0.0, 0.0, 1.0], {"kb": "Z", "source": "sz"})]
		)
		self.assertEqual(self.backend.info(self.name).count, 4)
		hit = self.backend.search(self.name, vector=[0.0, 0.0, 0.0, 1.0], limit=1)[0]
		self.assertEqual((hit.id, hit.text, hit.meta["kb"]), ("1", "replaced", "Z"))

	def test_duplicate_ids_in_one_batch_keep_the_last(self):
		self.ensure()
		self.backend.upsert(
			self.name,
			[
				Item("1", "first", [1.0, 0.0, 0.0, 0.0], {"kb": "A", "source": "s"}),
				Item("1", "second", [1.0, 0.0, 0.0, 0.0], {"kb": "A", "source": "s"}),
			],
		)
		self.assertEqual(self.backend.info(self.name).count, 1)
		self.assertEqual(self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0])[0].text, "second")

	def test_wrong_vector_width_rejected(self):
		self.ensure()
		with self.assertRaises(DimensionMismatch):
			self.backend.upsert(self.name, [Item("1", "x", [1.0, 0.0], {})])

	def test_undeclared_attribute_rejected(self):
		self.ensure()
		with self.assertRaises(ValueError):
			self.backend.upsert(self.name, [Item("1", "x", [1.0, 0.0, 0.0, 0.0], {"nope": "1"})])

	def test_empty_upsert_is_a_noop(self):
		self.ensure()
		self.backend.upsert(self.name, [])
		self.assertEqual(self.backend.info(self.name).count, 0)

	def test_quotes_and_unicode_survive(self):
		self.ensure()
		text = 'it\'s a "quoted" café — 日本語'
		self.backend.upsert(
			self.name, [Item("o'1", text, [1.0, 0.0, 0.0, 0.0], {"kb": "k'b", "source": "s"})]
		)
		hit = self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"kb": "k'b"})[0]
		self.assertEqual((hit.id, hit.text), ("o'1", text))
		self.backend.delete(self.name, ids=["o'1"])
		self.assertEqual(self.backend.info(self.name).count, 0)

	# -- vector search -----------------------------------------------------

	def test_search_missing_or_empty_collection_returns_nothing(self):
		self.assertEqual(self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0]), [])
		self.ensure()
		self.assertEqual(self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0]), [])

	def test_vector_search_orders_by_cosine_similarity(self):
		self.filled()
		hits = self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], limit=3)
		self.assertEqual(self.ids(hits)[:2], ["1", "3"])
		self.assertGreaterEqual(hits[0].score, hits[1].score)
		self.assertAlmostEqual(hits[0].score, 1.0, places=3)

	def test_limit_is_respected_and_clamped(self):
		self.filled()
		self.assertEqual(len(self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], limit=2)), 2)
		self.assertEqual(len(self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], limit=0)), 1)

	def test_query_vector_width_checked(self):
		self.filled()
		with self.assertRaises(DimensionMismatch):
			self.backend.search(self.name, vector=[1.0, 0.0])

	def test_search_needs_vector_or_text(self):
		self.filled()
		with self.assertRaises(ValueError):
			self.backend.search(self.name)

	# -- filters -----------------------------------------------------------

	def test_equality_filter(self):
		self.filled()
		hits = self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"kb": "B"}, limit=10)
		self.assertEqual(sorted(self.ids(hits)), ["3", "4"])
		self.assertTrue(all(h.meta["kb"] == "B" for h in hits))

	def test_in_filter_and_combined_filters(self):
		self.filled()
		hits = self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"kb": ["A", "B"]}, limit=10)
		self.assertEqual(len(hits), 4)
		hits = self.backend.search(
			self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"kb": ["A", "B"], "source": "s3"}, limit=10
		)
		self.assertEqual(self.ids(hits), ["3"])

	def test_empty_in_list_matches_nothing(self):
		self.filled()
		self.assertEqual(self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"kb": []}), [])

	def test_filter_on_undeclared_attribute_rejected(self):
		self.filled()
		with self.assertRaises(ValueError):
			self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"nope": "x"})

	def test_filter_value_that_looks_like_sql_is_inert(self):
		self.filled()
		hits = self.backend.search(self.name, vector=[1.0, 0.0, 0.0, 0.0], where={"kb": "A' OR '1'='1"})
		self.assertEqual(hits, [])

	# -- deletes -----------------------------------------------------------

	def test_delete_by_ids_and_by_where(self):
		self.filled()
		self.backend.delete(self.name, ids=["1"])
		self.assertEqual(self.backend.info(self.name).count, 3)
		self.backend.delete(self.name, where={"kb": "B"})
		self.assertEqual(self.backend.info(self.name).count, 1)

	def test_delete_combines_criteria(self):
		self.filled()
		self.backend.delete(self.name, ids=["1", "3"], where={"kb": "A"})
		self.assertEqual(self.backend.info(self.name).count, 3)  # only "1" matches both

	def test_delete_requires_a_criterion_and_empty_lists_are_noops(self):
		self.filled()
		with self.assertRaises(ValueError):
			self.backend.delete(self.name)
		self.backend.delete(self.name, ids=[])
		self.backend.delete(self.name, where={"kb": []})
		self.assertEqual(self.backend.info(self.name).count, 4)

	def test_delete_on_missing_collection_is_a_noop(self):
		self.backend.delete(self.name, ids=["1"])

	# -- keyword and hybrid search ----------------------------------------

	def test_hybrid_search_uses_text_and_vector(self):
		self.filled()
		hits = self.backend.search(self.name, vector=[0.0, 0.0, 0.0, 1.0], text="printer", limit=4)
		self.assertTrue(hits)
		if self.keyword:
			self.assertIn(hits[0].id, {"1", "3"})  # keyword match outranks the unrelated vector
		else:
			self.assertEqual(len(hits), 4)  # text is ignored, vector ranking only


class KeywordContract(BackendContract):
	"""Extra cases for backends with keyword ranking."""

	def test_keyword_only_search(self):
		self.filled()
		hits = self.backend.search(self.name, text="printer", limit=5)
		self.assertEqual(sorted(self.ids(hits)), ["1", "3"])

	def test_keyword_search_respects_filters(self):
		self.filled()
		hits = self.backend.search(self.name, text="printer", where={"kb": "B"}, limit=5)
		self.assertEqual(self.ids(hits), ["3"])

	def test_keyword_query_with_punctuation_and_no_tokens(self):
		self.filled()
		self.assertEqual(self.backend.search(self.name, text="!!! ???", limit=5), [])
		self.assertTrue(self.backend.search(self.name, text="printer's (toner); drop table", limit=5))

	def test_keyword_only_collection(self):
		self.backend.ensure_collection(
			self.name, dimension=None, identity={"kind": "text"}, filterable=["agent"]
		)
		self.backend.upsert(
			self.name,
			[
				Item("m1", "prefers email replies", None, {"agent": "a"}),
				Item("m2", "uses metric units", None, {"agent": "a"}),
			],
		)
		self.assertEqual(self.ids(self.backend.search(self.name, text="email", where={"agent": "a"})), ["m1"])
		with self.assertRaises(ValueError):
			self.backend.search(self.name, vector=[1.0], text="email")


class NoKeywordContract(BackendContract):
	keyword = False

	def test_keyword_only_search_is_unsupported(self):
		self.filled()
		with self.assertRaises(BackendUnavailable):
			self.backend.search(self.name, text="printer")

	def test_text_only_collections_are_unsupported(self):
		with self.assertRaises(BackendUnavailable):
			self.backend.ensure_collection(self.name, dimension=None, identity={}, filterable=[])


def _lancedb_available() -> bool:
	try:
		import lancedb
		import pyarrow
	except ImportError:
		return False
	return True


@unittest.skipUnless(_lancedb_available(), "lancedb is not installed")
class TestLanceDBBackend(KeywordContract, unittest.TestCase):
	def make_backend(self):
		from frappe_ai.vector.lancedb_backend import LanceDBBackend

		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		return LanceDBBackend(directory.name)


try:
	import chromadb

	_HAS_CHROMA = True
except ImportError:
	_HAS_CHROMA = False


@unittest.skipUnless(_HAS_CHROMA, "chromadb is not installed")
class TestChromaBackend(NoKeywordContract, unittest.TestCase):
	def make_backend(self):
		from frappe_ai.vector.chroma_backend import ChromaBackend

		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		return ChromaBackend(path=directory.name)


PG_DSN = os.environ.get("FRAPPE_AI_TEST_PG_DSN")


class _PsycopgRunner:
	"""SqlRunner over a plain psycopg connection (autocommit), for tests."""

	def __init__(self, dsn: str):
		import psycopg

		self._conn = psycopg.connect(dsn, autocommit=True)

	def execute(self, sql, params=()):
		self._conn.execute(sql, list(params))

	def fetchall(self, sql, params=()):
		return self._conn.execute(sql, list(params)).fetchall()

	def close(self):
		self._conn.close()


@unittest.skipUnless(PG_DSN, "set FRAPPE_AI_TEST_PG_DSN to a Postgres database with pgvector available")
class TestPgVectorBackend(KeywordContract, unittest.TestCase):
	def make_backend(self):
		from frappe_ai.vector.pgvector_backend import PgVectorBackend

		runner = _PsycopgRunner(PG_DSN)
		self.addCleanup(runner.close)
		return PgVectorBackend(runner)

	def test_wide_vectors_skip_the_hnsw_index(self):
		wide = self.name + "w"
		self.addCleanup(self.backend.drop, wide)
		self.backend.ensure_collection(wide, dimension=2500, identity={"k": 1}, filterable=[])
		self.backend.upsert(wide, [Item("1", "x", [0.5] * 2500, {})])
		self.assertEqual(self.backend.search(wide, vector=[0.5] * 2500)[0].id, "1")

	def test_identifiers_are_validated(self):
		for bad in ("a;b", "a b", '"x"', "1abc", ""):
			with self.assertRaises(ValueError):
				check_identifier(bad)
		with self.assertRaises(ValueError):
			self.backend.ensure_collection("bad name", dimension=4, identity={}, filterable=[])


class TestHelpers(unittest.TestCase):
	def test_postgres_array_literal_escapes_quotes_and_backslashes(self):
		from frappe_ai.vector.pgvector_backend import _text_array

		self.assertEqual(_text_array(["a", 'b"c', "d\\e", "it's"]), '{"a","b\\"c","d\\\\e","it\'s"}')
		self.assertEqual(_text_array([]), "{}")

	def test_rrf_fuse_rewards_agreement(self):
		scores = rrf_fuse([["a", "b", "c"], ["c", "a"]])
		self.assertEqual(sorted(scores, key=scores.get, reverse=True)[:2], ["a", "c"])


if __name__ == "__main__":
	unittest.main()
