# Progress — Optional Tender RAG and Embedding-Aware Models

## Overall status

Complete in code; focused verification passed against `tact.local`.

## Completed

- Added `AI Model.model_type` with `Chat` as the default and explicit `Embedding` selection.
- Routed model connection checks to chat completions or embeddings according to that type.
- Normalized provider-prefixed Gemini IDs only at transport time.
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

- Embedding configuration remains required by general `frappe_ai` knowledge sources.
- Only Tender Spec Review may fall back to cached direct extraction.
- Direct fallback context is capped at 100,000 characters per document.
