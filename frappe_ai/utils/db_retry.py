# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Retry a whole request when the database reports a transaction conflict.

Frappe runs Postgres transactions at REPEATABLE READ. When two requests lock the same row
(`for_update=True`, or an `UPDATE` of the same row) the second one does not wait and re-read, as it
does on MariaDB: it fails with "could not serialize access due to concurrent update" (SQLSTATE
40001). Deadlocks (40P01 on Postgres, 1213 on MariaDB) are the same kind of conflict. Neither is
a bug in the request: running it again in a new transaction sees the other request's result.

`retry_on_conflict` does exactly that. Only use it on entry points whose work before the first
write has no side effects outside the transaction, so a retry cannot repeat anything visible.
"""

from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

import frappe

#: SQLSTATE codes meaning "run the transaction again": serialization failure and deadlock.
CONFLICT_SQLSTATES = frozenset({"40001", "40P01"})
DEFAULT_ATTEMPTS = 6

F = TypeVar("F", bound=Callable[..., Any])


def is_conflict(error: BaseException | None) -> bool:
	"""True if `error`, or anything it was raised from, is a transaction conflict."""
	seen: set[int] = set()
	while error is not None and id(error) not in seen:
		seen.add(id(error))
		if (
			isinstance(error, frappe.QueryDeadlockError)
			or getattr(error, "pgcode", None) in CONFLICT_SQLSTATES
		):
			return True
		error = error.__cause__ or error.__context__
	return False


def retry_on_conflict(function: F | None = None, *, attempts: int = DEFAULT_ATTEMPTS) -> Any:
	"""Decorator: roll back and run again when the transaction conflicts, up to `attempts` times."""

	def decorate(target: F) -> F:
		@functools.wraps(target)
		def wrapper(*args: Any, **kwargs: Any) -> Any:
			for attempt in range(1, attempts + 1):
				try:
					return target(*args, **kwargs)
				except Exception as error:
					if attempt == attempts or not is_conflict(error):
						raise
					frappe.db.rollback()
					time.sleep(random.uniform(0.01, 0.05) * attempt)

		return wrapper  # type: ignore[return-value]

	return decorate(function) if function is not None else decorate
