# ADR 0021 — The app runs on MariaDB and Postgres

**Status:** Accepted
**Date:** 2026-11-03
**Deciders:** Sri Harsha Dabbiru

---

## Context

Frappe supports MariaDB and Postgres. `frappe_ai` was written and tested on MariaDB, and a
few places used MariaDB-only SQL. Deployments differ in which database they run, and a
vector index that lives in the same database (pgvector) is only possible on Postgres.

## Decision

Application code works on both databases:

1. Reach the database through the Frappe ORM (`get_doc`, `get_all`, `db_set`, `for_update`)
   or the query builder (`frappe.qb`). Do not write vendor SQL.
2. Raw SQL is allowed only in modules that choose the dialect themselves or are
   database-specific by design (`api/retirement.py`, `vector/registry.py`,
   `vector/pgvector_backend.py`). `tests/test_database_portability.py` fails when any other
   module uses `frappe.db.sql`, backticked table names, `ON DUPLICATE KEY`, `IFNULL`,
   `GROUP_CONCAT`, MariaDB date functions or `SHOW` statements.
3. `LIKE` patterns built from user or model text go through `utils/like.py`.
4. `JSON` fields are read with `frappe.parse_json`, which accepts both a string and an
   already-parsed value.
5. The chosen database does not decide behaviour except through a setting: the default
   vector backend is pgvector on Postgres and LanceDB otherwise (ADR 0022).

## Consequences

**Positive:** one codebase for both databases; new vendor SQL is caught in review by a test.
**Negative:** the full test suite should run on both databases, which doubles CI time.
Features that only one database offers must sit behind a setting or an adapter.

## Alternatives considered

- *MariaDB only.* Simplest, but rules out pgvector and Postgres-only deployments.
- *Postgres only.* Allows richer SQL but excludes existing MariaDB sites.

## Verification

- `python -m unittest frappe_ai.tests.test_database_portability` (needs no site).
- Run the app's test suite against a MariaDB site and a Postgres site.
