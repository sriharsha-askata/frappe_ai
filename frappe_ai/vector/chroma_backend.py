# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Chroma adapter (experimental).

Chroma stores vectors, documents and metadata, with cosine nearest-neighbour search and
metadata filters. It has no keyword ranking, so this backend declares
`supports_keyword_search = False`: a query with a vector and text ranks by vector only,
and a keyword-only query is rejected. Prefer pgvector or LanceDB where hybrid search
matters. The collection's dimension, identity and filterable attributes live in the
Chroma collection metadata.

The client is embedded (a local directory) or remote (`host`, `port`, `ssl`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from frappe_ai.vector.base import (
	BackendUnavailable,
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

PREFIX = "ai_vec_"
MARKER = {"_ai": "1"}  # Chroma rejects empty metadata dicts


class ChromaBackend(VectorBackend):
	name = "chroma"
	supports_keyword_search = False

	def __init__(
		self, *, path: str | None = None, host: str | None = None, port: int = 8000, ssl: bool = False
	):
		self._path, self._host, self._port, self._ssl = path, host, port, ssl
		self._client = None

	def _chroma(self):
		if self._client is None:
			try:
				import chromadb
			except ImportError as exc:
				raise BackendUnavailable("The chromadb package is not installed.") from exc
			if self._host:
				self._client = chromadb.HttpClient(host=self._host, port=self._port, ssl=self._ssl)
			else:
				self._client = chromadb.PersistentClient(path=self._path)
		return self._client

	@staticmethod
	def _name(collection: str) -> str:
		return PREFIX + check_identifier(collection)

	def _get(self, collection: str):
		try:
			return self._chroma().get_collection(self._name(collection))
		except Exception:
			return None

	@staticmethod
	def _meta(col) -> dict[str, Any]:
		raw = col.metadata or {}
		dim = raw.get("frappe_ai_dimension", 0)
		return {
			"dimension": int(dim) or None,
			"identity": json.loads(raw.get("frappe_ai_identity", "{}")),
			"filterable": json.loads(raw.get("frappe_ai_filterable", "[]")),
		}

	def ensure_collection(self, collection, *, dimension, identity, filterable, recreate_on_mismatch=False):
		if dimension is None:
			raise BackendUnavailable("Chroma has no keyword index; it cannot hold a text-only collection.")
		if not isinstance(dimension, int) or dimension < 1:
			raise ValueError(f"Invalid dimension: {dimension!r}")
		filterable = [check_identifier(f) for f in filterable]
		wanted = json.loads(json.dumps(dict(identity), sort_keys=True))
		existing = self._get(collection)
		if existing is not None:
			meta = self._meta(existing)
			problem = None
			if meta["dimension"] != dimension:
				problem = DimensionMismatch(
					f"Collection has dimension {meta['dimension']} but {dimension} was requested. Rebuild the index."
				)
			elif meta["identity"] != wanted or meta["filterable"] != filterable:
				problem = IdentityMismatch(
					"Index embedding identity does not match the configured one. Rebuild the index."
				)
			if problem is None:
				return
			if not recreate_on_mismatch:
				raise problem
			self._chroma().delete_collection(self._name(collection))
		self._chroma().create_collection(
			self._name(collection),
			metadata={
				"hnsw:space": "cosine",
				"frappe_ai_dimension": dimension,
				"frappe_ai_identity": json.dumps(wanted, sort_keys=True),
				"frappe_ai_filterable": json.dumps(filterable),
			},
		)

	def info(self, collection):
		col = self._get(collection)
		if col is None:
			return None
		meta = self._meta(col)
		return CollectionInfo(dimension=meta["dimension"], identity=meta["identity"], count=col.count())

	def drop(self, collection):
		if self._get(collection) is not None:
			self._chroma().delete_collection(self._name(collection))

	def upsert(self, collection, items):
		if not items:
			return
		col = self._get(collection)
		if col is None:
			raise VectorStoreNotReady(
				f"Collection {collection!r} does not exist; call ensure_collection first."
			)
		meta = self._meta(col)
		dimension, filterable = meta["dimension"], meta["filterable"]
		deduped: dict[str, Item] = {item.id: item for item in items}
		ids, vectors, documents, metadatas = [], [], [], []
		for item in deduped.values():
			unknown = set(item.meta) - set(filterable)
			if unknown:
				raise ValueError(f"Attributes not declared filterable on {collection!r}: {sorted(unknown)}")
			if item.vector is None:
				raise ValueError(f"Item {item.id!r} has no vector but {collection!r} is a vector collection")
			if len(item.vector) != dimension:
				raise DimensionMismatch(
					f"Vector has dimension {len(item.vector)}, but {collection!r} uses {dimension}."
				)
			ids.append(item.id)
			vectors.append([float(v) for v in item.vector])
			documents.append(item.text or "")
			metadatas.append({**MARKER, **{name: str(item.meta.get(name, "")) for name in filterable}})
		col.upsert(ids=ids, embeddings=vectors, documents=documents, metadatas=metadatas)

	def delete(self, collection, *, ids=None, where=None):
		if ids is None and where is None:
			raise ValueError("delete() requires ids and/or where")
		col = self._get(collection)
		if col is None:
			return
		if ids is not None and not list(ids):
			return
		clause = self._where(self._meta(col)["filterable"], where)
		if clause is None:
			return
		if ids is not None:
			col.delete(ids=[str(i) for i in ids], where=clause or None)
		elif clause:
			col.delete(where=clause)
		else:
			raise ValueError("delete() requires at least one non-empty criterion")

	def search(self, collection, *, vector=None, text=None, where=None, limit=5):
		if vector is None:
			if text:
				raise BackendUnavailable("Chroma has no keyword search; provide a vector.")
			raise ValueError("search() requires a vector and/or text")
		col = self._get(collection)
		if col is None:
			return []
		meta = self._meta(col)
		if len(vector) != meta["dimension"]:
			raise DimensionMismatch(
				f"Query vector has dimension {len(vector)}, but the index uses {meta['dimension']}."
			)
		clause = self._where(meta["filterable"], where)
		count = col.count()
		if clause is None or count == 0:
			return []
		result = col.query(
			query_embeddings=[[float(v) for v in vector]],
			n_results=min(clamp_limit(limit), count),
			where=clause or None,
			include=["documents", "metadatas", "distances"],
		)
		hits = []
		for item_id, doc, md, dist in zip(
			result["ids"][0],
			result["documents"][0],
			result["metadatas"][0],
			result["distances"][0],
			strict=True,
		):
			hits.append(
				Hit(
					id=item_id,
					score=1.0 - float(dist),
					text=doc or "",
					meta={n: (md or {}).get(n, "") for n in meta["filterable"]},
				)
			)
		return hits

	@staticmethod
	def _where(filterable: Sequence[str], where: Where | None) -> dict | None:
		"""Chroma `where` dict; {} when unfiltered; None when it can match nothing."""
		clauses = []
		for key, values in normalise_where(where).items():
			if key not in filterable:
				raise ValueError(f"Attribute {key!r} is not filterable on this collection")
			if not values:
				return None
			clauses.append({key: values[0]} if len(values) == 1 else {key: {"$in": values}})
		if not clauses:
			return {}
		return clauses[0] if len(clauses) == 1 else {"$and": clauses}
