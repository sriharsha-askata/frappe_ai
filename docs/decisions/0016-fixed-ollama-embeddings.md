# ADR 0016 — One fixed embedding model: `nomic-embed-text` on Ollama

**Status:** Accepted · **Date:** 2026-09-01

## The problem

Knowledge search compares vectors, and vectors from different embedding models cannot be compared. If an administrator could pick the embedding model in `AI Model` or `AI Settings`, one change would make every stored vector meaningless. It also tied deployments to provider-specific SDKs.

## The decision

Use **Ollama's OpenAI-compatible embeddings endpoint with the fixed model `nomic-embed-text`**. Only the address varies by environment, through `FRAPPE_AI_OLLAMA_BASE_URL` (default `http://localhost:11434/v1`). Ollama is contacted only when an embedding is actually needed, not as a health check.

- `AI Model` holds chat models only. `AI Settings` has no embedding-model choice, only the observed vector size (`embedding_dimension`, read-only), saved after the first successful embedding request.
- The LanceDB table records provider, model and vector size in its metadata, and reads and writes **reject a mismatch**.
- MariaDB remains the truth for chunk text and metadata. LanceDB stays a rebuildable index under `sites/<site>/private/files/lancedb/`, written only by Frappe ingestion workers.

## What follows

- Every embedding uses the same model and vector space.
- Ollama can be local or on a private host.
- If Ollama is down, normal chat still works. Large attachments fall back to being inserted into the prompt (shortened), and knowledge ingestion and search report the embedding error.
- The index must be rebuilt once when moving to this setup, and again if the Ollama model or its vector size changes.
- The first knowledge operation needs Ollama running.

## Migration

Back up MariaDB and LanceDB, install `nomic-embed-text` (`ollama pull nomic-embed-text`), then run `frappe_ai.knowledge.migration.rebuild_knowledge_index()` from a console. Check the row counts, the vector size and the search quality. Only then is the obsolete setting removed. The step is explicit because it needs a live embedding service and changes the index contents. Full steps: [setup](../setup.md).

## Related

[ADR 0002](0002-lancedb-vector-store.md), [setup](../setup.md), `frappe_ai/knowledge/embedder.py`, `store.py`, `migration.py`.
