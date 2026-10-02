# Progress — fixed Ollama embeddings

Decision: [ADR 0016](../decisions/0016-fixed-ollama-embeddings.md). Setup: [setup guide](../setup.md).

## Status

Implemented. Checked with mocked embedding responses. **Not yet run against a live Ollama server or a production migration.**

## What was done

- All embedding calls use Ollama's `nomic-embed-text`, with the address from `FRAPPE_AI_OLLAMA_BASE_URL` (default `http://localhost:11434/v1`).
- The embedding-model choice was removed from `AI Settings` and `AI Model`.
- The vector size seen on the first request is saved in `AI Settings.embedding_dimension`; both LanceDB vector tables record provider, model and size, and a mismatch is rejected.
- Normal chat does not depend on Ollama. If it is down, attachment search falls back to inserting shortened text; ingestion marks the source Failed and knowledge search reports the error.
- A helper rebuilds the LanceDB index from MariaDB (`rebuild_knowledge_index`), and a pre-model-sync patch detects an old configuration and prints an "ACTION REQUIRED" message.

## Follow-up fixes from the code review

Six issues in the migration and failure handling were fixed:

1. **Old LanceDB tables without metadata** are dropped when opened and reported as "Knowledge Store Not Ready", instead of failing every search with an uncaught mismatch error.
2. **The patch warns operators** (message on stderr naming `rebuild_knowledge_index`) instead of staying silent.
3. **Zero as "unset" for the dimension** is now explicit (`cint(existing)`), so the intent is clear.
4. **A temporary Ollama outage no longer permanently downgrades attachments.** Demotion to inline text is in memory only; the stored mode stays `Retrieval` and is retried on the next request. Real indexing failures (a broken file) still change the stored mode.
5. **The rebuild's count check** compares the indexed count with the chunks counted in the same run, so a job inserting a row during the rebuild no longer makes it fail.
6. **Embedding-model name detection** (`EMBEDDING_ID_MARKERS`) also matches dotted ids such as `cohere.embed.english-v3`.

## Checks

Fixed-model and endpoint tests, index metadata and size-mismatch tests, ingestion saving the size from a real response shape, attachment fallback, knowledge failure reporting and rebuild consistency tests pass.
