# Progress — Fixed Ollama embeddings

## Status

Implemented and verified against `tact.local` with mocked embedding responses.

## Completed

- Fixed all embedding calls to Ollama `nomic-embed-text`.
- Added `FRAPPE_AI_OLLAMA_BASE_URL` with a local default.
- Removed embedding selection from `AI Settings` and `AI Model`.
- Persisted the observed dimension in `AI Settings` and Ollama/model/dimension metadata
  in both LanceDB vector tables.
- Added explicit dimension/model mismatch checks.
- Kept normal chat independent from Ollama availability.
- Added safe inline fallback for attachment retrieval failures.
- Added source failure/search errors for unavailable embeddings.
- Added an explicit MariaDB-to-LanceDB rebuild helper for existing installations.
- Added the pre-schema migration hook that detects legacy embedding configuration.

## Verification

- Fixed-model and environment-endpoint tests pass.
- Knowledge index metadata and dimension mismatch tests pass.
- Ingestion initializes and persists dimension from the real response seam.
- Attachment fallback, knowledge failure reporting, and rebuild consistency tests pass.
- A live Ollama request and production migration have not been run in this environment.
