# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""The searchable index over knowledge chunks and chat attachments.

The relational database owns chunk text and metadata. This module keeps a disposable
copy of it in the active vector backend (`frappe_ai.vector`) and is the only place that
knows which collections exist, which attributes they filter on, and how backend errors
are shown to users. Two indexes are exposed:

* `chunks` - curated knowledge, filterable by knowledge base and source. A chunk's
  integer `AI Knowledge Chunk` name is its id here.
* `attachments` - oversized chat attachments, always searched within one session.

Both are rebuilt from the database, so switching backends means re-indexing.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _

from frappe_ai.knowledge.embedder import embedding_identity
from frappe_ai.vector.base import (
	BackendUnavailable,
	DimensionMismatch,
	IdentityMismatch,
	Item,
	VectorBackend,
	VectorStoreNotReady,
)
from frappe_ai.vector.registry import get_backend

MAX_CHUNK_SEARCH = 100
MAX_ATTACHMENT_SEARCH = 50


@contextmanager
def _translate_errors() -> Iterator[None]:
	"""Show vector-layer failures as ordinary validation errors."""
	try:
		yield
	except DimensionMismatch as error:
		frappe.throw(str(error), title=_("Embedding Dimension Mismatch"))
	except IdentityMismatch as error:
		frappe.throw(str(error), title=_("Embedding Index Mismatch"))
	except VectorStoreNotReady:
		frappe.throw(
			_("Knowledge store is not initialized. Ensure Ollama is available and ingest a source."),
			title=_("Knowledge Store Not Ready"),
		)
	except BackendUnavailable as error:
		frappe.throw(str(error), title=_("Vector Store Unavailable"))


class _Index:
	collection = ""
	filterable: tuple[str, ...] = ()
	recreate_on_mismatch = False

	@staticmethod
	def backend() -> VectorBackend:
		return get_backend()

	def ensure(self, dimension: int) -> None:
		"""Create the index for this vector width, or validate the existing one."""
		if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1:
			raise ValueError(f"Invalid embedding dimension: {dimension!r}")
		with _translate_errors():
			self.backend().ensure_collection(
				self.collection,
				dimension=dimension,
				identity=embedding_identity(dimension),
				filterable=self.filterable,
				recreate_on_mismatch=self.recreate_on_mismatch,
			)

	def exists(self) -> bool:
		return self.backend().info(self.collection) is not None

	def dimension(self) -> int | None:
		info = self.backend().info(self.collection)
		return info.dimension if info else None

	def metadata(self) -> dict[str, Any]:
		"""The embedding identity the index was built with, or {} if it does not exist."""
		info = self.backend().info(self.collection)
		return dict(info.identity) if info else {}

	def count(self) -> int:
		info = self.backend().info(self.collection)
		return info.count if info else 0

	def drop(self) -> None:
		self.backend().drop(self.collection)

	def _upsert(self, items: Sequence[Item]) -> None:
		if items:
			with _translate_errors():
				self.backend().upsert(self.collection, items)


class ChunkIndex(_Index):
	collection = "chunks"
	filterable = ("kb", "source")

	def add(self, rows: Sequence[dict[str, Any]]) -> None:
		"""Index rows of `{id, kb, source, content, vector}`. The index must exist (`ensure`)."""
		self._upsert(
			[
				Item(
					id=str(row["id"]),
					text=row["content"],
					vector=row["vector"],
					meta={"kb": row["kb"], "source": row["source"]},
				)
				for row in rows
			]
		)

	def delete(
		self, *, kb: str | None = None, source: str | None = None, ids: Sequence[int] | None = None
	) -> None:
		"""Delete rows matching ALL given criteria; at least one is required."""
		if kb is None and source is None and ids is None:
			raise ValueError("delete() requires at least one of kb, source, ids")
		where = {key: value for key, value in (("kb", kb), ("source", source)) if value is not None}
		with _translate_errors():
			self.backend().delete(
				self.collection,
				ids=[str(i) for i in ids] if ids is not None else None,
				where=where or None,
			)

	def search(
		self,
		vector: Sequence[float],
		*,
		text: str | None = None,
		kbs: Sequence[str] | None = None,
		limit: int = 5,
	) -> list[dict[str, Any]]:
		"""Nearest chunks, optionally fused with keyword matches and scoped to knowledge bases.

		Returns `[{id, kb, source, score}]` with a higher score meaning a better match."""
		with _translate_errors():
			hits = self.backend().search(
				self.collection,
				vector=vector,
				text=text or None,
				where={"kb": list(kbs)} if kbs else None,
				limit=min(int(limit), MAX_CHUNK_SEARCH),
			)
		return [
			{"id": int(hit.id), "kb": hit.meta["kb"], "source": hit.meta["source"], "score": hit.score}
			for hit in hits
		]

	def ids(self) -> set[int]:
		"""Every indexed chunk id, for reconciling against `AI Knowledge Chunk`."""
		return {int(i) for i in self.backend().list_ids(self.collection)}


class AttachmentIndex(_Index):
	collection = "chat_attachment_chunks"
	filterable = ("session", "attachment")
	# Chat chunks are ephemeral and rebuildable, so a changed embedding setup just resets them.
	recreate_on_mismatch = True

	def add(
		self, session: str, attachment: str, chunks: Sequence[str], vectors: Sequence[Sequence[float]]
	) -> None:
		"""Index one attachment's chunks. The index must exist (`ensure`)."""
		if len(chunks) != len(vectors):
			raise ValueError(f"chunks/vectors length mismatch: {len(chunks)} != {len(vectors)}")
		self._upsert(
			[
				Item(
					id=f"{attachment}:{position}",
					text=content,
					vector=vector,
					meta={"session": session, "attachment": attachment},
				)
				for position, (content, vector) in enumerate(zip(chunks, vectors, strict=True))
			]
		)

	def search(
		self, vector: Sequence[float], *, session: str, text: str | None = None, limit: int = 8
	) -> list[dict[str, Any]]:
		"""Best chunks within one session, most relevant first, as `[{content, score}]`.

		A session scope is mandatory: there is no unscoped search."""
		if not session:
			raise ValueError("search requires a session scope")
		with _translate_errors():
			hits = self.backend().search(
				self.collection,
				vector=vector,
				text=text or None,
				where={"session": session},
				limit=min(int(limit), MAX_ATTACHMENT_SEARCH),
			)
		return [{"content": hit.text, "score": hit.score} for hit in hits]

	def delete(self, *, session: str | Sequence[str] | None = None, attachment: str | None = None) -> None:
		"""Delete rows matching ALL given criteria; `session` may be one id or a list."""
		if session is None and attachment is None:
			raise ValueError("delete() requires session and/or attachment")
		where: dict[str, Any] = {}
		if session is not None:
			where["session"] = session if isinstance(session, str) else list(session)
		if attachment is not None:
			where["attachment"] = attachment
		with _translate_errors():
			self.backend().delete(self.collection, where=where)


chunks = ChunkIndex()
attachments = AttachmentIndex()
