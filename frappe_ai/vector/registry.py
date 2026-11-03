# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Choose and build the active vector backend for the current site.

`AI Settings.vector_backend` names the backend: `Auto` (the default), a built-in name
(`lancedb`, `pgvector`, `chroma`), or a name an installed app registers through the
`frappe_ai_vector_backends` hook:

	frappe_ai_vector_backends = {"qdrant": "my_app.vectors.make_qdrant"}

A hook value is the dotted path of a zero-argument factory returning a `VectorBackend`.
`Auto` picks pgvector on Postgres and LanceDB elsewhere. Importing this module needs no
Frappe site; Frappe is only touched when a backend is resolved or built.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from frappe_ai.vector.base import BackendUnavailable, VectorBackend

HOOK = "frappe_ai_vector_backends"
SETTING = "vector_backend"
AUTO = "auto"
BUILTIN = ("lancedb", "pgvector", "chroma")

_cache: dict[tuple[Any, ...], VectorBackend] = {}


class FrappeSqlRunner:
	"""`SqlRunner` over `frappe.db`.

	Every statement, DDL included, runs inside the current transaction (`frappe.db.sql`, not
	`sql_ddl`, which commits first). Postgres DDL is transactional, so a new index table is
	created or rolled back together with the chunk rows that go with it."""

	def execute(self, sql, params=()):
		import frappe

		frappe.db.sql(sql, tuple(params) or None)

	def fetchall(self, sql, params=()):
		import frappe

		return [tuple(row) for row in frappe.db.sql(sql, tuple(params) or None)]


def _make_lancedb() -> VectorBackend:
	import frappe

	from frappe_ai.vector.lancedb_backend import LanceDBBackend

	name = "lancedb_test" if frappe.flags.in_test else "lancedb"
	return LanceDBBackend(frappe.get_site_path("private", "files", name))


def _make_pgvector() -> VectorBackend:
	import frappe

	from frappe_ai.vector.pgvector_backend import PgVectorBackend

	if frappe.db.db_type != "postgres":
		raise BackendUnavailable("The pgvector backend needs a Postgres site database.")
	return PgVectorBackend(FrappeSqlRunner())


def _make_chroma() -> VectorBackend:
	import frappe

	from frappe_ai.vector.chroma_backend import ChromaBackend

	url = (frappe.conf.get("frappe_ai_chroma_url") or "").strip()
	if url:
		from urllib.parse import urlparse

		parsed = urlparse(url if "//" in url else f"//{url}")
		return ChromaBackend(host=parsed.hostname, port=parsed.port or 8000, ssl=parsed.scheme == "https")
	return ChromaBackend(path=frappe.get_site_path("private", "files", "chroma"))


_BUILTIN_FACTORIES: dict[str, Callable[[], VectorBackend]] = {
	"lancedb": _make_lancedb,
	"pgvector": _make_pgvector,
	"chroma": _make_chroma,
}


def _hook_factories() -> dict[str, str]:
	"""Backends registered by installed apps: `{name: dotted factory path}`."""
	import frappe

	hooks = frappe.get_hooks(HOOK) or {}
	entries = [hooks] if isinstance(hooks, dict) else [e for e in hooks if isinstance(e, dict)]
	found: dict[str, str] = {}
	for entry in entries:
		for key, value in entry.items():
			found[str(key).lower()] = value[-1] if isinstance(value, list | tuple) else value
	return found


def available_backends() -> list[str]:
	"""Names that may be selected: the built-ins plus any hook-registered backends."""
	return [*BUILTIN, *(n for n in sorted(_hook_factories()) if n not in BUILTIN)]


def default_backend_name() -> str:
	"""What `Auto` resolves to for this site's database."""
	import frappe

	return "pgvector" if frappe.db.db_type == "postgres" else "lancedb"


def configured_name() -> str:
	"""The active backend name, validated against what is available."""
	import frappe

	raw = (frappe.db.get_single_value("AI Settings", SETTING) or AUTO).strip().lower()
	name = default_backend_name() if raw == AUTO else raw
	if name not in available_backends():
		raise BackendUnavailable(
			f"Unknown vector backend {raw!r}. Choose Auto or one of: {', '.join(available_backends())}."
		)
	return name


def get_backend() -> VectorBackend:
	"""The active backend, built once per site and process."""
	import frappe

	name = configured_name()
	key = (frappe.local.site, name, frappe.conf.get("frappe_ai_chroma_url"))
	backend = _cache.get(key)
	if backend is None:
		factory = _BUILTIN_FACTORIES.get(name)
		if factory is None:
			factory = frappe.get_attr(_hook_factories()[name])
		backend = factory()
		_cache[key] = backend
	return backend


def reset_cache() -> None:
	"""Forget built backends (after a settings change or in tests)."""
	_cache.clear()
