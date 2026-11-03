# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Keep the app database-independent (MariaDB and Postgres). Needs no Frappe site.

Application code reaches the database through the Frappe ORM or query builder. Statements
that use one vendor's syntax are only allowed in the few places listed below, each of which
chooses the right dialect itself.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from frappe_ai.utils.like import contains, escape_like

APP = Path(__file__).resolve().parents[1]

#: Modules allowed to issue raw SQL, with the reason.
RAW_SQL_ALLOWED = {
	"api/retirement.py": "DDL that picks the identifier quote for the active database",
	"vector/registry.py": "runs the vector backend's own SQL through frappe.db",
	"vector/pgvector_backend.py": "Postgres-only backend",
}

VENDOR_SYNTAX = [
	(re.compile(r"`tab[A-Z]"), "backtick-quoted table name (MariaDB only)"),
	(re.compile(r"\bON\s+DUPLICATE\s+KEY\b", re.I), "ON DUPLICATE KEY (MariaDB only)"),
	(re.compile(r"\bINSERT\s+IGNORE\b", re.I), "INSERT IGNORE (MariaDB only)"),
	(re.compile(r"\bGROUP_CONCAT\b", re.I), "GROUP_CONCAT (MariaDB only)"),
	(re.compile(r"\bIFNULL\s*\(", re.I), "IFNULL (use COALESCE or the query builder)"),
	(re.compile(r"\bSTR_TO_DATE\b|\bDATE_FORMAT\b", re.I), "MariaDB date functions"),
	(re.compile(r"\bSHOW\s+(TABLES|COLUMNS|INDEX)\b", re.I), "SHOW statements (MariaDB only)"),
]

RAW_SQL_CALL = re.compile(r"\bfrappe\.db\.(sql|sql_ddl|multisql)\s*\(")


def _app_sources():
	for path in sorted(APP.rglob("*.py")):
		relative = path.relative_to(APP).as_posix()
		if relative.startswith("tests/") or "/tests/" in relative or "__pycache__" in relative:
			continue
		yield relative, path.read_text(encoding="utf-8")


class TestDatabasePortability(unittest.TestCase):
	def test_no_vendor_specific_sql_outside_allowed_modules(self):
		offences = []
		for relative, source in _app_sources():
			if relative in RAW_SQL_ALLOWED:
				continue
			for pattern, label in VENDOR_SYNTAX:
				if pattern.search(source):
					offences.append(f"{relative}: {label}")
		self.assertEqual(offences, [], "Use the ORM or query builder, or add the module to RAW_SQL_ALLOWED.")

	def test_raw_sql_calls_are_confined_to_allowed_modules(self):
		offences = [
			relative
			for relative, source in _app_sources()
			if relative not in RAW_SQL_ALLOWED and RAW_SQL_CALL.search(source)
		]
		self.assertEqual(offences, [], "Prefer frappe.qb / frappe.get_all over frappe.db.sql.")

	def test_allowed_modules_exist(self):
		for relative in RAW_SQL_ALLOWED:
			self.assertTrue((APP / relative).exists(), relative)


class TestLikeHelpers(unittest.TestCase):
	def test_wildcards_and_escape_character_are_escaped(self):
		self.assertEqual(escape_like("50%_off\\"), "50\\%\\_off\\\\")

	def test_plain_text_is_unchanged(self):
		self.assertEqual(escape_like("invoice"), "invoice")

	def test_contains_wraps_the_escaped_text(self):
		self.assertEqual(contains("a_b"), "%a\\_b%")


if __name__ == "__main__":
	unittest.main()
