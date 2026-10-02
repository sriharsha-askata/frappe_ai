# Progress — optional tender RAG and embedding-aware models (superseded)

This work tried an `AI Model.model_type` split and embedding-aware model checks. It was **superseded** by [fixed Ollama embeddings](fixed-ollama-embeddings.md) ([ADR 0016](../decisions/0016-fixed-ollama-embeddings.md)): `AI Model` now holds chat models only and embeddings are not selected through any DocType. The `model_type` field, the type-routed checks and the migration backfill for embedding-named models are gone.

## What still applies from this work

- Provider endpoint and credential resolution is shared by chat and knowledge calls.
- Gemini ids written as `gemini/<name>` are normalized to `<name>` only at the transport boundary (`normalize_transport_model_id`); stored ids are not changed.
- **Tender Spec Review** may fall back to cached direct extraction when retrieval is unavailable, with the context capped at 100,000 characters per document. No other workflow has this fallback.
