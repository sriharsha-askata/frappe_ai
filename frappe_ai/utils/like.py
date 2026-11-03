# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Build `LIKE` patterns from user text.

`%` and `_` are wildcards in a `LIKE` pattern, so text typed by a user or produced by a
model must be escaped before it is matched literally. Backslash is the default escape
character on both MariaDB and Postgres.
"""

from __future__ import annotations

_ESCAPES = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def escape_like(text: str) -> str:
	"""Return `text` with the LIKE wildcards and the escape character escaped."""
	return text.translate(_ESCAPES)


def contains(text: str) -> str:
	"""Pattern matching any value that contains `text` literally."""
	return f"%{escape_like(text)}%"
