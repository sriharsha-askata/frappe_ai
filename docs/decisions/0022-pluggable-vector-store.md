# ADR 0022 — Pluggable vector store

**Status:** Accepted
**Date:** 2026-11-03
**Deciders:** Sri Harsha Dabbiru
**Supersedes:** [0002](0002-lancedb-vector-store.md)

---

## Context

Search over knowledge chunks and chat attachments needs a vector index with filters, and
ideally keyword ranking so hybrid search works. LanceDB was the only option and three modules
talked to it directly. A Postgres site can keep vectors in the same database (pgvector), with
one transaction and one backup; other deployments may prefer an external service.

## Decision

1. **The database stays authoritative** (ADR 0017). The vector backend holds a disposable,
   rebuildable copy of chunk text and vectors.
2. **One small interface** in `frappe_ai/vector/base.py`: `ensure_collection`, `info`,
   `upsert`, `delete`, `search`, `list_ids`, `drop`. Filters are an AND of attribute
   equalities or lists. Search takes a vector, text, or both (hybrid, fused by reciprocal
   rank). Adapters never see SQL fragments from callers.
3. **Adapters:** `pgvector` (Postgres tables, HNSW cosine index, generated `tsvector`),
   `lancedb` (embedded files, native keyword and hybrid search), `chroma` (experimental,
   vector search only). Apps can add more through the `frappe_ai_vector_backends` hook.
4. **Selection** is `AI Settings.vector_backend`: `Auto` (pgvector on Postgres, LanceDB
   otherwise), a built-in name, or a hook-registered name. Changing it means rebuilding the
   index with `frappe_ai.knowledge.reindex.rebuild_knowledge_index`.
5. **One Frappe-aware service**, `knowledge/index.py`, owns the collections (`chunks`,
   `chat_attachment_chunks`), the embedding identity check and the user-facing errors.
6. **Memories are not indexed.** At most a few hundred rows are already scoped by agent and
   user, so they are ranked in Python, which also expresses the "shared or mine" rule that an
   AND-only filter cannot.

## Defaults and trade-offs

| Backend | Use when | Notes |
|---|---|---|
| pgvector | Postgres site | Same transaction and backup as the chunks; filters are SQL; needs the `vector` extension; HNSW only up to 2000 dimensions |
| LanceDB | MariaDB site, single server | No service to run; real keyword + hybrid search; local files, one writer |
| Chroma | Experimental | No keyword ranking, so hybrid degrades to vector search |

## Consequences

**Positive:** swapping backends is a setting plus a rebuild; backends are tested by one
shared contract suite; the Frappe-free core is testable without a site.
**Negative:** every adapter is code to maintain; scores differ between backends, so retrieval
quality should be re-checked after a switch; pgvector DDL runs inside the Frappe transaction
and is committed straight after it is created.

## Alternatives considered

- *Keep LanceDB only.* No change, but ties search to local files and MariaDB-style deployments.
- *A hosted vector service only.* More operations work for every site.

## Verification

- `frappe_ai/tests/test_vector_contract.py` runs the same suite against each adapter (pgvector
  needs `FRAPPE_AI_TEST_PG_DSN`).
- Bench tests: `test_vector_registry.py`, the knowledge tests, and a backend switch plus rebuild.
