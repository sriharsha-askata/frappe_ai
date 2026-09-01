# Progress — Embedding migration bug fixes

## Status

In progress.

## Background

A post-implementation code review of the fixed-Ollama embedding change (see
`docs/progress/fixed-ollama-embeddings.md`) surfaced 7 bugs. All are in the migration /
failure-handling layer, not in the core embedding path itself.

## Decisions

### 1. Legacy LanceDB tables — drop on open, not validate-and-throw

Pre-migration LanceDB tables have no `frappe_ai.embedding_*` schema metadata. The original
`_open_table()` calls `_validate_index_metadata(table)`, which throws `frappe.ValidationError`
when metadata is absent. The KB retrieval call chain has no catch, so every knowledge search
fails post-upgrade with "Embedding Index Mismatch".

**Decision:** In both `store._open_table()` and `attachment_store._open_table()`, check
`index_metadata_for_table(table)` before calling `_validate_index_metadata`. An empty dict
(no keys at all) means a legacy table — drop it immediately (the table is explicitly declared
disposable) and raise "Knowledge Store Not Ready". This gives operators the same clear
actionable message as "store not initialized", without an uncaught exception in the KB search
path. Non-empty-but-wrong metadata still reaches `_validate_index_metadata` unchanged.

### 2. Patch must warn operators

The `pre_model_sync` patch resets `embedding_dimension=0` and returns silently, giving
operators no indication that knowledge search is broken until the first user hits the error.

**Decision:** In `migrate_fixed_embedding_configuration.execute()`, check the return status
and print a prominent `ACTION REQUIRED` block to `sys.stderr` when legacy configuration was
found, naming `rebuild_knowledge_index` as the required follow-up.

### 3. Falsy-zero guard — explicit cint conversion

`if existing and int(existing) != dimension` in `_record_embedding_dimension` uses a
falsy-zero guard. `0` is intentionally the "unset" sentinel, so the bypass is correct
semantics, but the code reads as though it might be a bug. Replace with `cint(existing)` (the
same Frappe utility used everywhere else in the app) so the intent is clear to future readers.

### 4. Permanent Inline demotion — ephemeral only for transient errors

`_demote_retrieval_attachments()` calls `row.db_set("mode", "Inline", update_modified=False)`,
which permanently writes to DB. A transient Ollama outage during `build_prompt_messages`
therefore permanently downgrades attachments; when Ollama recovers the user must re-upload.

**Decision:** Remove `db_set` from `_demote_retrieval_attachments` entirely — keep only
`row.mode = "Inline"` (in-memory). The DB retains `Retrieval`; the next request reloads and
retries. In `_index_retrieval_attachments`, catch `EmbeddingServiceUnavailable` separately
from generic exceptions: service-unavailable failures do in-memory demotion only; all other
exceptions (broken file, corrupt data) keep the permanent `db_set`.

### 5. Rebuild count race

`rebuild_knowledge_index` compares `chunk_count` (loop total) against `frappe.db.count()`
(live count at end). A concurrent `ingest_source` job that inserts one row mid-rebuild makes
`actual_count > chunk_count`, triggering "Rebuild Incomplete" for a correctly built index.

**Decision:** Remove `actual_count` from the post-rebuild check. Compare only
`indexed_count != chunk_count` — both are local to this rebuild run and not affected by
concurrent writes.

### 6. EMBEDDING_ID_MARKERS — add dot separator

`EMBEDDING_ID_MARKERS = ("embedding", "embed-")` misses model IDs that use a dot separator
(e.g. `cohere.embed.english-v3`). Add `"embed."` to the tuple.

## Completed

- [x] Phase 1: `store._open_table` and `attachment_store._open_table` — drop legacy tables on absent metadata; raise "Knowledge Store Not Ready" instead of "Embedding Index Mismatch"
- [x] Phase 2: Patch `execute()` — prints ACTION REQUIRED block to stderr when legacy configuration found
- [x] Phase 3: `_record_embedding_dimension` — explicit `cint()` guard, intent of zero-as-sentinel made clear
- [x] Phase 4: `_demote_retrieval_attachments` — ephemeral in-memory demotion only (no `db_set`); `_index_retrieval_attachments` retains `db_set` since data was never indexed
- [x] Phase 5: `rebuild_knowledge_index` — removed `actual_count` live-count race; comparison is now `indexed_count != chunk_count` only
- [x] Phase 6: `EMBEDDING_ID_MARKERS` — added `"embed."` to catch dot-separator IDs

## Remaining

Nothing. All phases complete.
