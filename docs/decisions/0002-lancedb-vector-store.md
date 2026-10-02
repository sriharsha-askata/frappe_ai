# ADR 0002 — LanceDB for search vectors, MariaDB as the truth

**Status:** Accepted · **Date:** 2026-08-05

## The problem

Agents need to search knowledge (documents) and recall memories. That needs a store for embeddings plus keyword search. The first design notes suggested ChromaDB; the older app `flow` already used LanceDB, which is installed in the bench.

## The decision

Use **LanceDB**, embedded in the site's files, as a **derived, rebuildable index**. MariaDB stays the single source of truth.

| Store | Holds | Authoritative? |
|---|---|---|
| MariaDB | All DocTypes, chunk text and metadata | **Yes** |
| LanceDB (`sites/<site>/private/files/lancedb`) | Embedding vectors and keyword (BM25) indexes | No: can be rebuilt from MariaDB |

Three tables share the folder:

- `chunks`: curated knowledge. The row `id` is the `AI Knowledge Chunk` name.
- `chat_attachment_chunks`: temporary chunks of large chat attachments, scoped to a session.
- `memories`: agent memories, keyword index only (no vectors).

Rules that follow from this:

- `AI Knowledge Chunk` is named by an **auto-increment integer**, because that number *is* the LanceDB `id`. Changing the naming breaks retrieval.
- **Only Frappe code reads and writes LanceDB.** Ingestion runs in Frappe background workers (one writer at a time); search runs inside the Frappe-side `search_knowledge` tool. The FastAPI service never touches the files ([001](../specifications/001-architecture.md)).
- `AI Settings.search_type` is `Hybrid` (vector plus keyword, the default) or `Vector`.

## What follows

**Good**
- **Hybrid search.** Keyword plus vector fusion finds exact codes and rare names that pure vector search misses. ChromaDB has no equivalent.
- **Memory recall by keywords** keeps working (top-12 selection once more than 20 memories exist).
- **No new dependency**, and the pipeline's escaping, distance settings and failure handling were already debugged in `flow`.
- Deleting the folder loses nothing: rebuild from MariaDB.

**Costs**
- It is a local file store, so Frappe web and worker processes must share the disk and keep to a single-writer rule. Scaling to several machines needs shared storage or a different store.
- Backups must cover the LanceDB folder (or accept a rebuild).

## Alternatives rejected

| Alternative | Why not |
|---|---|
| ChromaDB | No native hybrid search, would force a rewrite of ingest, retrieval and memory, and adds a dependency |
| Agno's built-in knowledge layer | Frappe would lose the `AI Knowledge Chunk` rows and the UI-managed sources, which are the point of building inside Frappe |
| MariaDB vector support | Immature, and would put embedding search on the ERP database |

## How to check

- Ingesting a PDF creates `AI Knowledge Chunk` rows *and* LanceDB rows with the same ids; deleting the source removes both.
- Deleting the LanceDB folder and re-ingesting (or `rebuild_knowledge_index`) gives the same search results.
- A keyword-heavy query does better with `Hybrid` than with `Vector`.

## Related

[001 §6](../specifications/001-architecture.md), [003 Knowledge](../specifications/003-doctype-reference.md#knowledge), [ADR 0016](0016-fixed-ollama-embeddings.md), [setup](../setup.md).
