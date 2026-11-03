# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""LanceDB adapter: an embedded, file-based index with native keyword and hybrid search.

One LanceDB table per collection. The collection's dimension, embedding identity and
filterable attributes are recorded in the table's schema metadata, so a table can be
validated without any external registry. `lancedb` and `pyarrow` are imported lazily,
so a deployment that selects another backend never loads them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from frappe_ai.vector.base import (
	CollectionInfo,
	DimensionMismatch,
	Hit,
	IdentityMismatch,
	Item,
	VectorBackend,
	VectorStoreNotReady,
	Where,
	check_identifier,
	clamp_limit,
	normalise_where,
)

META_DIMENSION = b"frappe_ai.dimension"
META_IDENTITY = b"frappe_ai.identity"
META_FILTERABLE = b"frappe_ai.filterable"
TEXT_FIELD = "text"


def _quote(value: str) -> str:
	return "'" + str(value).replace("'", "''") + "'"


class LanceDBBackend(VectorBackend):
	name = "lancedb"
	supports_keyword_search = True

	def __init__(self, path: str):
		self._path = path

	# -- lifecycle ---------------------------------------------------------

	def _db(self):
		import lancedb

		return lancedb.connect(self._path)

	def _names(self, db) -> list[str]:
		return list(db.list_tables().tables)

	def _open(self, collection: str):
		db = self._db()
		if check_identifier(collection) not in self._names(db):
			return None
		return db.open_table(collection)

	@staticmethod
	def _read_meta(table) -> dict[str, Any]:
		raw = table.schema.metadata or {}
		dimension = raw.get(META_DIMENSION)
		return {
			"dimension": int(dimension) if dimension not in (None, b"", b"none") else None,
			"identity": json.loads(raw.get(META_IDENTITY, b"{}")),
			"filterable": json.loads(raw.get(META_FILTERABLE, b"[]")),
		}

	def ensure_collection(self, collection, *, dimension, identity, filterable, recreate_on_mismatch=False):
		import pyarrow as pa

		filterable = [check_identifier(f) for f in filterable]
		if dimension is not None and (not isinstance(dimension, int) or dimension < 1):
			raise ValueError(f"Invalid dimension: {dimension!r}")
		db = self._db()
		wanted_identity = json.loads(json.dumps(dict(identity), sort_keys=True))
		if check_identifier(collection) in self._names(db):
			meta = self._read_meta(db.open_table(collection))
			problem = None
			if meta["dimension"] != dimension:
				problem = DimensionMismatch(
					f"Collection has dimension {meta['dimension']} but {dimension} was requested. Rebuild the index."
				)
			elif meta["identity"] != wanted_identity or meta["filterable"] != filterable:
				problem = IdentityMismatch(
					"Index embedding identity does not match the configured one. Rebuild the index."
				)
			if problem is None:
				return
			if not recreate_on_mismatch:
				raise problem
			db.drop_table(collection)

		fields = [pa.field("id", pa.string()), pa.field(TEXT_FIELD, pa.string())]
		fields += [pa.field(name, pa.string()) for name in filterable]
		if dimension is not None:
			fields.append(pa.field("vector", pa.list_(pa.float32(), dimension)))
		schema = pa.schema(
			fields,
			metadata={
				META_DIMENSION: str(dimension).encode() if dimension is not None else b"none",
				META_IDENTITY: json.dumps(wanted_identity, sort_keys=True).encode(),
				META_FILTERABLE: json.dumps(filterable).encode(),
			},
		)
		db.create_table(collection, schema=schema, exist_ok=True)

	def info(self, collection):
		table = self._open(collection)
		if table is None:
			return None
		meta = self._read_meta(table)
		return CollectionInfo(
			dimension=meta["dimension"], identity=meta["identity"], count=table.count_rows()
		)

	def list_ids(self, collection):
		table = self._open(collection)
		if table is None:
			return set()
		return set(table.to_arrow().column("id").to_pylist())

	def drop(self, collection):
		db = self._db()
		if check_identifier(collection) in self._names(db):
			db.drop_table(collection)

	# -- writes ------------------------------------------------------------

	def upsert(self, collection, items):
		if not items:
			return
		table = self._open(collection)
		if table is None:
			raise VectorStoreNotReady(
				f"Collection {collection!r} does not exist; call ensure_collection first."
			)
		meta = self._read_meta(table)
		dimension, filterable = meta["dimension"], meta["filterable"]
		deduped: dict[str, Item] = {item.id: item for item in items}
		rows = []
		for item in deduped.values():
			unknown = set(item.meta) - set(filterable)
			if unknown:
				raise ValueError(f"Attributes not declared filterable on {collection!r}: {sorted(unknown)}")
			row: dict[str, Any] = {"id": item.id, TEXT_FIELD: item.text or ""}
			row.update({name: str(item.meta.get(name, "")) for name in filterable})
			if dimension is not None:
				if item.vector is None:
					raise ValueError(
						f"Item {item.id!r} has no vector but {collection!r} is a vector collection"
					)
				if len(item.vector) != dimension:
					raise DimensionMismatch(
						f"Vector has dimension {len(item.vector)}, but {collection!r} uses {dimension}."
					)
				row["vector"] = [float(v) for v in item.vector]
			rows.append(row)
		table.delete(f"id IN ({', '.join(_quote(i) for i in deduped)})")
		table.add(rows)
		self._ensure_fts(table)

	def delete(self, collection, *, ids=None, where=None):
		if ids is None and where is None:
			raise ValueError("delete() requires ids and/or where")
		table = self._open(collection)
		if table is None:
			return
		if ids is not None and not list(ids):
			return
		predicate = self._predicate(self._read_meta(table)["filterable"], where)
		if predicate is None:
			return
		clauses = [predicate] if predicate else []
		if ids is not None:
			clauses.append(f"id IN ({', '.join(_quote(i) for i in ids)})")
		if not clauses:
			raise ValueError("delete() requires at least one non-empty criterion")
		table.delete(" AND ".join(clauses))

	# -- reads -------------------------------------------------------------

	def search(self, collection, *, vector=None, text=None, where=None, limit=5):
		if vector is None and not text:
			raise ValueError("search() requires a vector and/or text")
		table = self._open(collection)
		if table is None:
			return []
		meta = self._read_meta(table)
		dimension, filterable = meta["dimension"], meta["filterable"]
		if vector is not None:
			if dimension is None:
				raise ValueError(f"{collection!r} is keyword-only; search without a vector")
			if len(vector) != dimension:
				raise DimensionMismatch(
					f"Query vector has dimension {len(vector)}, but the index uses {dimension}."
				)
		predicate = self._predicate(filterable, where)
		if predicate is None or table.count_rows() == 0:
			return []
		limit = clamp_limit(limit)
		if text:
			self._ensure_fts(table)

		if vector is not None and text:
			query = (
				table.search(query_type="hybrid", vector_column_name="vector")
				.vector([float(v) for v in vector])
				.text(text)
			)
			query = query.distance_type("cosine")
			score_of = lambda r: r["_relevance_score"]  # noqa: E731
		elif vector is not None:
			query = table.search([float(v) for v in vector], vector_column_name="vector").distance_type(
				"cosine"
			)
			score_of = lambda r: 1.0 - r["_distance"]  # noqa: E731
		else:
			query = table.search(text, query_type="fts")
			score_of = lambda r: r["_score"]  # noqa: E731
		if predicate:
			query = query.where(predicate, prefilter=True)
		rows = query.limit(limit).to_list()
		return [
			Hit(
				id=r["id"],
				score=float(score_of(r)),
				text=r.get(TEXT_FIELD, ""),
				meta={n: r.get(n, "") for n in filterable},
			)
			for r in rows
		]

	# -- internals ---------------------------------------------------------

	@staticmethod
	def _predicate(filterable: Sequence[str], where: Where | None) -> str | None:
		"""SQL predicate for `where`; '' when unfiltered; None when it can match nothing."""
		parts = []
		for key, values in normalise_where(where).items():
			if key not in filterable:
				raise ValueError(f"Attribute {key!r} is not filterable on this collection")
			if not values:
				return None
			parts.append(f"{key} IN ({', '.join(_quote(v) for v in values)})")
		return " AND ".join(parts)

	@staticmethod
	def _ensure_fts(table) -> None:
		if any(str(index.index_type) == "FTS" for index in table.list_indices()):
			return
		table.create_fts_index(TEXT_FIELD, replace=True)
