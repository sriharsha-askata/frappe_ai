# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Backend-neutral contract for the disposable vector / keyword index.

The relational database (MariaDB or Postgres) stays authoritative for text and
metadata (ADR 0002, 0017). A vector backend only holds a rebuildable index of it, so
backends can be swapped by re-indexing. This module has no Frappe dependency: it is
the contract every adapter implements and what the contract tests exercise.

A *collection* is one index with a fixed set of filterable attributes, for example
``chunks`` (filter by ``kb``, ``source``). Items carry an optional vector and always a
text, so a collection can serve vector search, keyword search, or both fused.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: A filter maps an attribute to one value (equality) or a list of values (IN).
#: All entries are ANDed. An empty list matches nothing.
Where = Mapping[str, "str | Sequence[str]"]

MAX_LIMIT = 100


class VectorStoreError(Exception):
	"""Base class for backend failures that callers may translate for users."""


class VectorStoreNotReady(VectorStoreError):
	"""The collection does not exist yet (nothing has been indexed)."""


class DimensionMismatch(VectorStoreError):
	"""A vector's width differs from the collection's."""


class IdentityMismatch(VectorStoreError):
	"""The collection was built with a different embedding identity; rebuild it."""


class BackendUnavailable(VectorStoreError):
	"""The backend cannot be used here (missing extension, package or service)."""


@dataclass(frozen=True)
class Item:
	"""One indexed row. `meta` keys must be declared filterable on the collection."""

	id: str
	text: str = ""
	vector: Sequence[float] | None = None
	meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Hit:
	"""A search result. Higher `score` is better; scores are only comparable within one call."""

	id: str
	score: float
	text: str = ""
	meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionInfo:
	"""What a collection was built with. `dimension` is None for keyword-only collections."""

	dimension: int | None
	identity: Mapping[str, Any]
	count: int


class VectorBackend(ABC):
	"""Adapter contract. Implementations must enforce `where` themselves; callers rely on it
	for authorisation scoping (knowledge base, session, agent/user)."""

	name: str = ""
	#: True when `search(text=...)` ranks by keywords (BM25 or similar).
	supports_keyword_search: bool = True

	@abstractmethod
	def ensure_collection(
		self,
		collection: str,
		*,
		dimension: int | None,
		identity: Mapping[str, Any],
		filterable: Sequence[str],
		recreate_on_mismatch: bool = False,
	) -> None:
		"""Create the collection, or validate an existing one.

		Raise `DimensionMismatch` / `IdentityMismatch` when it exists with a different
		width or identity, unless `recreate_on_mismatch`, which drops and recreates it
		(for ephemeral indexes such as chat attachments)."""

	@abstractmethod
	def info(self, collection: str) -> CollectionInfo | None:
		"""Return the collection's build info, or None if it does not exist."""

	@abstractmethod
	def upsert(self, collection: str, items: Sequence[Item]) -> None:
		"""Insert or replace items by id. The collection must exist."""

	@abstractmethod
	def delete(
		self, collection: str, *, ids: Sequence[str] | None = None, where: Where | None = None
	) -> None:
		"""Delete rows matching ALL criteria. At least one criterion is required.
		A missing collection is a no-op; an empty `ids` list or empty IN list is a no-op."""

	@abstractmethod
	def search(
		self,
		collection: str,
		*,
		vector: Sequence[float] | None = None,
		text: str | None = None,
		where: Where | None = None,
		limit: int = 5,
	) -> list[Hit]:
		"""Rank rows. `vector` only: nearest by cosine. `text` only: keyword ranking.
		Both: reciprocal-rank fusion of the two. A missing or empty collection returns [].
		A backend without keyword search ignores `text` when `vector` is given and raises
		`BackendUnavailable` when only `text` is given."""

	@abstractmethod
	def list_ids(self, collection: str) -> set[str]:
		"""Return every id in the collection (empty for a missing one). Used to reconcile the
		index against the authoritative database."""

	@abstractmethod
	def drop(self, collection: str) -> None:
		"""Delete the collection and everything in it. A missing collection is a no-op."""


def clamp_limit(limit: int, maximum: int = MAX_LIMIT) -> int:
	return max(1, min(int(limit), maximum))


def normalise_where(where: Where | None) -> dict[str, list[str]]:
	"""Return `where` as `{attribute: [values]}` (equality becomes a one-element list)."""
	out: dict[str, list[str]] = {}
	for key, value in (where or {}).items():
		out[key] = [value] if isinstance(value, str) else [str(v) for v in value]
	return out


def rrf_fuse(rankings: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
	"""Reciprocal-rank fusion: each ranking contributes 1 / (k + rank) per id."""
	scores: dict[str, float] = {}
	for ranking in rankings:
		for rank, item_id in enumerate(ranking, start=1):
			scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
	return scores


def check_identifier(name: str) -> str:
	"""Collection and attribute names become SQL identifiers; allow only a safe subset."""
	if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
		raise ValueError(f"Unsafe identifier: {name!r}")
	return name
