# ADR 0016 — Fixed Ollama embeddings

**Status:** Accepted
**Date:** 2026-09-01

## Context

Knowledge retrieval needs one stable vector space across Frappe workers, the FastAPI
service, and site-scoped LanceDB indexes. Selecting an embedding model through `AI Model`
or `AI Settings` allowed a configuration change to make existing vectors meaningless and
made deployment depend on provider-specific SDK configuration.

## Decision

Use Ollama's OpenAI-compatible embeddings endpoint with the fixed model
`nomic-embed-text`. Only `FRAPPE_AI_OLLAMA_BASE_URL` varies by environment; it defaults to
`http://localhost:11434/v1`. The endpoint is checked only when a real embedding request
is needed.

`AI Model` contains chat models only. `AI Settings` retains the observed vector
dimension as a read-only consistency value but no embedding-model selector. The first
successful embedding request persists that dimension. LanceDB schema metadata records
provider, model, and dimension, and reads/writes reject mismatches.

MariaDB remains authoritative for `AI Knowledge Chunk` text and metadata. LanceDB remains
rebuildable derived storage under `sites/<site>/private/files/lancedb/`; Frappe ingestion
workers remain the single writer.

## Consequences

- Every embedding call has the same model identity and vector space.
- Environments can place Ollama locally or on a private internal host.
- Missing Ollama does not stop ordinary chat. Attachment retrieval falls back to capped
  inline content, while knowledge ingestion/search report the embedding failure.
- Existing indexes must be rebuilt once, and again if the Ollama model digest or vector
  dimension changes.
- Running the first knowledge operation requires Ollama and records the returned width.

## Migration

Back up MariaDB and LanceDB, install `nomic-embed-text`, run
`frappe_ai.knowledge.migration.rebuild_knowledge_index()`, verify counts/dimensions and
retrieval quality, then remove the obsolete setting. The migration is explicit because
it needs a live embedding service and changes derived index contents.
