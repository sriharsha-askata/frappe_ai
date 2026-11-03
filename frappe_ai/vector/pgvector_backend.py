# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Postgres + pgvector adapter.

Vectors and keyword (tsvector) data live in ordinary Postgres tables next to the
authoritative chunk rows, so a Frappe transaction covers both and one database backup
covers everything. SQL is issued through a small `SqlRunner`, which is `frappe.db` in
the app and a plain psycopg connection in tests.

Layout, per collection `c`: table `ai_vec_c` with `id text primary key`, `text`, a
nullable `vector vector(N)`, one `text` column per filterable attribute, and a generated
`tsv tsvector`. `ai_vec_registry` records each collection's dimension, identity and
filterable attributes.

Notes:
- Run `CREATE EXTENSION vector` once as a privileged role; this adapter tries it only if
  the extension is missing and raises `BackendUnavailable` with a clear message if that fails.
- Keyword search uses the `simple` text-search configuration (no language stemming), which
  keeps behaviour predictable across languages. Query words are OR-ed.
- An HNSW cosine index is created when the dimension is at most 2000 (pgvector's limit for
  `vector`); wider collections fall back to an exact scan.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

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
	rrf_fuse,
)

REGISTRY = "ai_vec_registry"
MAX_HNSW_DIMENSION = 2000
UPSERT_BATCH = 200
CANDIDATE_FACTOR = 4
MAX_QUERY_TOKENS = 32
_TOKEN = re.compile(r"\w+", re.UNICODE)


class SqlRunner(Protocol):
	"""Minimal SQL access. Placeholders are `%s`; avoid a literal `%` in statements."""

	def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

	def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]: ...


def _table(collection: str) -> str:
	return f'"ai_vec_{check_identifier(collection)}"'


def _vector_literal(vector: Sequence[float]) -> str:
	return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def _tsquery(text: str) -> str | None:
	tokens = list(dict.fromkeys(t.lower() for t in _TOKEN.findall(text or "")))[:MAX_QUERY_TOKENS]
	return " | ".join(tokens) if tokens else None


class PgVectorBackend(VectorBackend):
	name = "pgvector"
	supports_keyword_search = True
	transactional_ddl = True

	def __init__(self, sql: SqlRunner):
		self._sql = sql

	# -- lifecycle ---------------------------------------------------------

	def ensure_collection(self, collection, *, dimension, identity, filterable, recreate_on_mismatch=False):
		table = _table(collection)
		filterable = [check_identifier(f) for f in filterable]
		if dimension is not None and (not isinstance(dimension, int) or dimension < 1):
			raise ValueError(f"Invalid dimension: {dimension!r}")
		self._ensure_extension()
		self._ensure_registry()

		existing = self._registry_row(collection)
		if existing:
			problem = self._mismatch(existing, dimension, identity, filterable)
			if problem is None:
				return
			if not recreate_on_mismatch:
				raise problem
			self.drop(collection)

		columns = ['"id" text primary key', "\"text\" text not null default ''"]
		columns += [f"\"{name}\" text not null default ''" for name in filterable]
		if dimension is not None:
			columns.append(f'"vector" vector({int(dimension)})')
		columns.append('"tsv" tsvector generated always as (to_tsvector(\'simple\', "text")) stored')
		self._sql.execute(f"create table if not exists {table} ({', '.join(columns)})")
		bare = table.strip('"')
		self._sql.execute(f'create index if not exists "{bare}_tsv" on {table} using gin ("tsv")')
		for name in filterable:
			self._sql.execute(f'create index if not exists "{bare}_{name}" on {table} ("{name}")')
		if dimension is not None and dimension <= MAX_HNSW_DIMENSION:
			self._sql.execute(
				f'create index if not exists "{bare}_vec" on {table} using hnsw ("vector" vector_cosine_ops)'
			)
		self._sql.execute(
			f"insert into {REGISTRY} (collection, dimension, identity, filterable) values (%s, %s, %s, %s) "
			"on conflict (collection) do update set dimension = excluded.dimension, "
			"identity = excluded.identity, filterable = excluded.filterable",
			(collection, dimension, json.dumps(dict(identity), sort_keys=True), json.dumps(filterable)),
		)

	def info(self, collection):
		row = self._registry_row(collection)
		if not row:
			return None
		(count,) = self._sql.fetchall(f"select count(*) from {_table(collection)}")[0]
		return CollectionInfo(dimension=row["dimension"], identity=row["identity"], count=int(count))

	def list_ids(self, collection):
		if not self._registry_row(collection):
			return set()
		return {row[0] for row in self._sql.fetchall(f'select "id" from {_table(collection)}')}

	def drop(self, collection):
		self._sql.execute(f"drop table if exists {_table(collection)}")
		if self._registry_exists():
			self._sql.execute(f"delete from {REGISTRY} where collection = %s", (collection,))

	# -- writes ------------------------------------------------------------

	def upsert(self, collection, items):
		if not items:
			return
		row = self._require(collection)
		filterable, dimension = row["filterable"], row["dimension"]
		deduped: dict[str, Item] = {item.id: item for item in items}
		prepared = []
		for item in deduped.values():
			unknown = set(item.meta) - set(filterable)
			if unknown:
				raise ValueError(f"Attributes not declared filterable on {collection!r}: {sorted(unknown)}")
			if dimension is not None:
				if item.vector is None:
					raise ValueError(
						f"Item {item.id!r} has no vector but {collection!r} is a vector collection"
					)
				if len(item.vector) != dimension:
					raise DimensionMismatch(
						f"Vector has dimension {len(item.vector)}, but {collection!r} uses {dimension}."
					)
			prepared.append(item)

		columns = ["id", "text", *filterable] + (["vector"] if dimension is not None else [])
		column_sql = ", ".join(f'"{c}"' for c in columns)
		slot = "(%s, %s" + ", %s" * len(filterable) + (", %s::vector" if dimension is not None else "") + ")"
		assignments = ", ".join(f'"{c}" = excluded."{c}"' for c in columns[1:])
		for start in range(0, len(prepared), UPSERT_BATCH):
			batch = prepared[start : start + UPSERT_BATCH]
			params: list[Any] = []
			for item in batch:
				params += [item.id, item.text or ""]
				params += [str(item.meta.get(name, "")) for name in filterable]
				if dimension is not None:
					params.append(_vector_literal(item.vector))
			self._sql.execute(
				f"insert into {_table(collection)} ({column_sql}) values {', '.join([slot] * len(batch))} "
				f'on conflict ("id") do update set {assignments}',
				params,
			)

	def delete(self, collection, *, ids=None, where=None):
		if ids is None and where is None:
			raise ValueError("delete() requires ids and/or where")
		row = self._registry_row(collection)
		if not row:
			return
		if ids is not None and not list(ids):
			return
		clauses, params = self._where(row["filterable"], where)
		if clauses is None:
			return
		if ids is not None:
			clauses.append('"id" = any(%s::text[])')
			params.append([str(i) for i in ids])
		if not clauses:
			raise ValueError("delete() requires at least one non-empty criterion")
		self._sql.execute(f"delete from {_table(collection)} where {' and '.join(clauses)}", params)

	# -- reads -------------------------------------------------------------

	def search(self, collection, *, vector=None, text=None, where=None, limit=5):
		if vector is None and not text:
			raise ValueError("search() requires a vector and/or text")
		row = self._registry_row(collection)
		if not row:
			return []
		dimension, filterable = row["dimension"], row["filterable"]
		if vector is not None:
			if dimension is None:
				raise ValueError(f"{collection!r} is keyword-only; search without a vector")
			if len(vector) != dimension:
				raise DimensionMismatch(
					f"Query vector has dimension {len(vector)}, but the index uses {dimension}."
				)
		clauses, params = self._where(filterable, where)
		if clauses is None:
			return []
		limit = clamp_limit(limit)
		candidates = limit * CANDIDATE_FACTOR if (vector is not None and text) else limit
		columns = ", ".join(f'"{c}"' for c in ["id", "text", *filterable])
		table = _table(collection)
		where_sql = (" and " + " and ".join(clauses)) if clauses else ""

		vector_rows: list[tuple] = []
		if vector is not None:
			literal = _vector_literal(vector)
			vector_rows = self._sql.fetchall(
				f'select {columns}, 1 - ("vector" <=> %s::vector) as score from {table} '
				f'where "vector" is not null{where_sql} order by "vector" <=> %s::vector limit %s',
				[literal, *params, literal, candidates],
			)

		text_rows: list[tuple] = []
		query = _tsquery(text) if text else None
		if query:
			text_rows = self._sql.fetchall(
				f"select {columns}, ts_rank_cd(\"tsv\", to_tsquery('simple', %s)) as score from {table} "
				f"where \"tsv\" @@ to_tsquery('simple', %s){where_sql} order by score desc limit %s",
				[query, query, *params, candidates],
			)

		names = ["id", "text", *filterable]

		def to_hit(r: tuple, score: float) -> Hit:
			return Hit(
				id=r[0],
				score=float(score),
				text=r[1],
				meta=dict(zip(names[2:], r[2 : len(names)], strict=True)),
			)

		if vector is not None and not query:
			return [to_hit(r, r[-1]) for r in vector_rows[:limit]]
		if vector is None:
			return [to_hit(r, r[-1]) for r in text_rows[:limit]]

		fused = rrf_fuse([[r[0] for r in vector_rows], [r[0] for r in text_rows]])
		by_id = {r[0]: r for r in [*text_rows, *vector_rows]}
		ranked = sorted(fused, key=lambda i: fused[i], reverse=True)[:limit]
		return [to_hit(by_id[i], fused[i]) for i in ranked]

	# -- internals ---------------------------------------------------------

	def _ensure_extension(self) -> None:
		if self._sql.fetchall("select 1 from pg_extension where extname = 'vector'"):
			return
		try:
			self._sql.execute("create extension if not exists vector")
		except Exception as exc:
			raise BackendUnavailable(
				"The pgvector extension is not installed. Install it on the Postgres server and run "
				"`CREATE EXTENSION vector;` as a privileged role."
			) from exc

	def _ensure_registry(self) -> None:
		self._sql.execute(
			f"create table if not exists {REGISTRY} (collection text primary key, dimension integer, "
			"identity text not null, filterable text not null)"
		)

	def _registry_exists(self) -> bool:
		return bool(self._sql.fetchall("select 1 from pg_tables where tablename = %s", (REGISTRY,)))

	def _registry_row(self, collection: str) -> dict[str, Any] | None:
		if not self._registry_exists():
			return None
		rows = self._sql.fetchall(
			f"select dimension, identity, filterable from {REGISTRY} where collection = %s", (collection,)
		)
		if not rows:
			return None
		dimension, identity, filterable = rows[0]
		return {
			"dimension": dimension,
			"identity": json.loads(identity),
			"filterable": json.loads(filterable),
		}

	def _require(self, collection: str) -> dict[str, Any]:
		row = self._registry_row(collection)
		if not row:
			raise VectorStoreNotReady(
				f"Collection {collection!r} does not exist; call ensure_collection first."
			)
		return row

	@staticmethod
	def _mismatch(row, dimension, identity, filterable) -> Exception | None:
		if row["dimension"] != dimension:
			return DimensionMismatch(
				f"Collection has dimension {row['dimension']} but {dimension} was requested. Rebuild the index."
			)
		if row["identity"] != json.loads(json.dumps(dict(identity), sort_keys=True)):
			return IdentityMismatch(
				"Index embedding identity does not match the configured one. Rebuild the index."
			)
		if row["filterable"] != filterable:
			return IdentityMismatch("Index attributes differ from the expected ones. Rebuild the index.")
		return None

	@staticmethod
	def _where(filterable: Sequence[str], where: Where | None):
		"""Return (clauses, params), or (None, None) when the filter can match nothing."""
		clauses: list[str] = []
		params: list[Any] = []
		for key, values in normalise_where(where).items():
			if key not in filterable:
				raise ValueError(f"Attribute {key!r} is not filterable on this collection")
			if not values:
				return None, None
			clauses.append(f'"{key}" = any(%s::text[])')
			params.append(values)
		return clauses, params
