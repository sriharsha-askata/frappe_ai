# ADR 0017 — MariaDB as source of truth, LanceDB as disposable index

**Status:** Accepted
**Date:** 2026-09-03
**Deciders:** Sri Harsha Dabbiru

---

## Context

The knowledge system stores chunk text and embeddings across two storage layers:

1. **MariaDB** — `AI Knowledge Chunk` doctype holds chunk text, metadata, and relationships
2. **LanceDB** — local vector + FTS index under `sites/<site>/private/files/lancedb/`

This ADR records the explicit decision to keep MariaDB as the authoritative store and treat
LanceDB as a rebuildable derived index.

---

## Decision

**MariaDB (`AI Knowledge Chunk`) is the authoritative store.** LanceDB is a disposable,
rebuildable index derived from MariaDB.

| Layer | Store | Contents | Authoritative? |
|-------|-------|---------|---------------|
| Primary | MariaDB | `AI Knowledge Chunk` — `knowledge_base`, `source`, `chunk_index`, `content`, `content_hash`, `reference_doctype`, `reference_name` | **Yes** |
| Derived | LanceDB | `chunks` table — `id`, `kb`, `source`, `content`, `vector` | No — rebuildable |

The LanceDB row `id` is the MariaDB chunk's autoincrement `name`. Every vector hit maps
back to its MariaDB row via this key.

---

## Rationale

### Why MariaDB stays authoritative

1. **Durability** — LanceDB lives in `<site>/private/files/lancedb/`, a file on disk. It is
   not included in MariaDB backups. If the site is migrated, rebuilt, or the file is
   corrupted, vectors are lost. MariaDB survives all of these.

2. **Incremental updates** — DocType sources rely on `content_hash` in MariaDB to skip
   re-embedding unchanged rows. Without MariaDB as the source, you'd need to duplicate that
   logic in LanceDB or rebuild the entire index on every sync.

3. **Query flexibility** — MariaDB supports SQL joins, filters, and direct inspection. You
   can query "show all chunks from source X" or join chunks with their reference documents.
   LanceDB is a niche format without these capabilities.

4. **Version migration** — If the embedding model or dimension changes (per ADR 0016), the
   LanceDB index must be rebuilt. Having the raw chunk text in MariaDB means you can
   re-embed from the authoritative source. Without MariaDB, you'd need to re-extract from
   the original File/URL/DocType every time.

### Why LanceDB exists anyway

1. **Speed** — Vector similarity search over 768-dim embeddings is fast in LanceDB, slower in
   MariaDB without native vector support.

2. **Hybrid search** — LanceDB provides native BM25 + vector fusion (rank-fused). This
   was a key reason LanceDB was chosen over ChromaDB (ADR 0002).

3. **Full-text index** — LanceDB's built-in FTS (`content` field) enables keyword fallback
   when vector search alone would miss exact matches.

---

## Consequences

### Positive

- **Vectors are recoverable** — `drop_table()` + re-ingestion rebuilds LanceDB from MariaDB.
- **No data loss on site migration** — chunk text survives in MariaDB; only vectors need
  rebuilding.
- **Clean separation of concerns** — MariaDB holds the "what", LanceDB optimizes the "how".

### Negative

- **Redundancy** — chunk text lives in both MariaDB (`AI Knowledge Chunk.content`) and
  LanceDB (`chunks.content`). This is intentional, not a bug.
- **Two-step retrieval** — after LanceDB search, you must look up the MariaDB row by `id`
  to get full chunk details (including page markers in `content`).

---

## Page/Section Metadata Note

The chunk `content` field includes `--- PAGE N ---` markers extracted during document
processing (via `frappe_ai.knowledge.extract._export_docling_markdown_with_pages()`). These
markers are **embedded in the text**, not stored as structured fields.

LanceDB's `search()` returns only `{id, kb, source, score}` — it does **not** return
`content`. To recover page numbers after a vector search:

1. Take the returned `id`
2. Look up the MariaDB `AI Knowledge Chunk` row
3. Parse the `content` field for `--- PAGE N ---` markers

Section names are not stored anywhere — `build_section_index()` in tender_automation
detects them heuristically from the full document text, but that information is not
preserved in the chunk store.

---

## Migration

No migration needed — this is the current state. If LanceDB is ever lost:

```python
# Rebuild LanceDB from MariaDB chunks
from frappe_ai.knowledge import store
from frappe_ai.knowledge.embedder import embed_texts

# Fetch all chunks from MariaDB
chunks = frappe.get_all("AI Knowledge Chunk", fields=["name", "knowledge_base", "source", "content"])

# Re-embed and write to LanceDB
vectors = embed_texts([c.content for c in chunks])
store.ensure_table_for_dimension(len(vectors[0]))
rows = [
    {"id": int(c.name), "kb": c.knowledge_base, "source": c.source, "content": c.content, "vector": v}
    for c, v in zip(chunks, vectors)
]
store.add(rows)
```

---

## References

- [ADR 0002 — LanceDB as the vector store](0002-lancedb-vector-store.md)
- [ADR 0016 — Fixed Ollama embeddings](0016-fixed-ollama-embeddings.md)
- `frappe_ai/knowledge/store.py` — LanceDB operations
- `frappe_ai/knowledge/ingest.py` — MariaDB → LanceDB sync
- `frappe_ai/knowledge/extract.py` — document extraction with page markers
