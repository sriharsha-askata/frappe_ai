# ADR 0002 — LanceDB as the vector store

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Context

`flow` uses **LanceDB** for all vector and full-text search, in three site-scoped tables
sharing one DB path under `private/files/lancedb`:

| Table | Purpose | Vectors? |
|---|---|---|
| `chunks` | Curated knowledge base search | yes |
| `chat_attachment_chunks` | Ephemeral oversized session attachments | yes |
| `memories` | Agent memory recall | **no** — BM25 FTS only |

LanceDB provides **native hybrid search** (BM25 + vector with relevance fusion), which
`flow` exposes as `search_type` and defaults to `Hybrid`. LanceDB **0.36.0 is already
installed** in this bench.

The reference architecture specification for this migration nominated ChromaDB. That
option was evaluated and rejected — see *Alternatives Considered*.

---

## Decision

**Keep LanceDB** as the vector store for `frappe_ai`.

The store is opened by the FastAPI service for retrieval, and by Frappe background workers
for ingestion. Both processes address the same site-scoped LanceDB path.

| Store | Contents | Authoritative? |
|---|---|---|
| MariaDB | All DocTypes, chunk text, chunk metadata | **Yes** |
| LanceDB | Embedding vectors + FTS indexes | No — rebuildable from MariaDB |

Tables carry over from `flow` unchanged:

- `chunks` — curated knowledge, `id` = `AI Knowledge Chunk` autoincrement name
- `chat_attachment_chunks` — ephemeral, scoped by session
- `memories` — vectorless BM25 index over agent memories

---

## Consequences

### Positive

- **Hybrid search is preserved.** BM25 + vector fusion continues to work, so queries
  dominated by exact keywords, product codes, or rare proper nouns retrieve as well as they
  do in `flow`. This was the single largest functional risk of the Chroma path, and it is
  avoided entirely.
- **BM25 memory recall is preserved.** `AI Agent Memory` keeps its dedicated FTS index and
  top-12 relevance selection above the 20-memory injection threshold. No degradation to
  keyword matching.
- **No new dependency.** LanceDB 0.36.0 is installed and in use. Nothing to add, nothing to
  version-pin against `chromadb`.
- **The retrieval pipeline ports nearly as-is.** `flow/knowledge/store.py`,
  `attachment_store.py`, `retriever.py`, and `memory/store.py` migrate with mechanical
  renaming rather than a rewrite. This removes an entire class of migration risk from
  Phase 4 and materially reduces its size.
- **Proven at this exact workload.** The escaping rules (`_quote`), cosine distance
  configuration, relevance scoring, and index-failure degradation paths are all already
  debugged in `flow`.

### Negative

- **The vector store is shared across process boundaries.** Ingestion runs in Frappe
  background workers; retrieval runs in the FastAPI service. Both touch the same LanceDB
  path on disk. LanceDB tolerates concurrent readers with a single writer, and the design
  keeps writes in Frappe workers only — but this constraint must be respected and is worth
  a comment at every write site.
- **Embedded store, not a service.** LanceDB is a local file store, so the FastAPI service
  is not fully stateless with respect to disk: it needs read access to the site's private
  files. This weakens the "deploy the service anywhere" property of
  [ADR 0001](0001-agno-fastapi-over-frappe-native.md). Horizontal scaling requires either
  shared storage or moving retrieval behind a Frappe-side endpoint.
- **Diverges from the reference specification**, which named ChromaDB. This ADR is the
  record of that divergence and its rationale.

### Neutral

- MariaDB remains authoritative. `AI Knowledge Chunk` keeps `autoincrement` naming because
  its integer name **is** the LanceDB row `id` — load-bearing, unchanged from `flow`.
- `AI Settings.search_type` keeps both `Hybrid` and `Vector`, defaulting to **`Hybrid`** as
  in `flow`.

---

## Alternatives Considered

### ChromaDB (the reference specification's choice)
Rejected. It would have cost:
- **Hybrid search entirely** — Chroma has no native BM25+vector fusion.
- **BM25 memory recall** — replaced by MariaDB `LIKE`/fulltext, less precise.
- **A full rewrite** of ingest/retrieval/attachment/memory storage, versus a near-direct port.
- **A new dependency**, while LanceDB stayed installed for other apps anyway.

The upside was alignment with the reference document and a marginally cleaner
service-ownership story. Not worth losing two working search capabilities and rewriting a
debugged pipeline.

### Agno's native knowledge layer
Agno abstracts over several vector DBs, including LanceDB. Rejected because Frappe would
lose the `AI Knowledge Chunk` audit rows and UI-managed sources unless re-added on top —
surrendering the DocType-driven management that is a primary reason to build inside Frappe.

Worth revisiting narrowly: Agno's LanceDB adapter could sit *over* the same tables if it
proves compatible with the `id`-as-chunk-name convention.

### pgvector / MariaDB-native vectors
Would consolidate storage and keep hybrid search in SQL. Rejected: this bench runs MariaDB,
whose vector support is immature relative to purpose-built stores, and it would put
embedding search load directly on the ERP database.

---

## Verification

- Ingesting a PDF produces `AI Knowledge Chunk` rows **and** LanceDB entries with matching ids.
- Deleting a source purges both.
- Dropping the LanceDB directory and re-running ingestion reproduces identical retrieval —
  proving MariaDB is genuinely authoritative.
- `search_type = Hybrid` measurably outperforms `Vector` on a keyword-heavy query,
  confirming fusion is active rather than silently falling back.
- Concurrent ingestion (background worker) and retrieval (service) against the same table
  do not corrupt or deadlock.

---

## References

- [001 — Architecture §7](../specifications/001-architecture.md)
- [003 — DocType Reference §14](../specifications/003-doctype-reference.md)
- `apps/flow/flow/knowledge/` — the pipeline being ported
- `apps/flow/flow/memory/store.py` — the vectorless FTS index being ported
