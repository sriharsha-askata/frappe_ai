# Progress — Optional Tender RAG and Embedding-Aware Models

> Superseded for embedding configuration by [Fixed Ollama embeddings](fixed-ollama-embeddings.md).

## Overall status

Complete in code; focused verification passed against `tact.local`.

## Completed

- Added the historical `AI Model.model_type` split; this is removed by the fixed Ollama architecture.
- Routed the historical model connection checks by type; embedding checks are now separate from `AI Model`.
- Normalized provider-prefixed Gemini IDs only at the historical transport boundary.
- Preserved provider endpoint and credential resolution for chat and knowledge transports.
- Added an idempotent after-migrate backfill for unambiguously embedding-named legacy models.
- Prevented embedding models from being used as chat agent models.

## Verification

- AI Model embedding connection test: 1/1.
- OpenAI-compatible transport tests: 12/12.
- Embedding/model-type knowledge tests pass; the full 139-test knowledge run was
  blocked by three unrelated Redis Queue connection failures.
- Service model configuration tests: 8/8.
- Full app suite still contains unrelated pre-existing migration/fixture failures.

## Decisions

- Fixed Ollama embedding configuration is application-wide; it is not required in `AI Settings`.
- Only Tender Spec Review may fall back to cached direct extraction.
- Direct fallback context is capped at 100,000 characters per document.
