# LanceDB connection is re-established on every single operation

**Type:** Improvement (efficiency)
**Severity:** Low
**Status:** Open — not yet scoped
**Source:** Architecture review, 2026-09-06 (Finding L-2)

---

## Description

`knowledge/store.py:_connect()` (:41-42) calls `lancedb.connect(db_path())` fresh
on every operation — `table_exists`, `index_metadata`, `add`, `delete`, `search`,
`drop_table`, and `_open_table` all call `_connect()` independently, with no cached
handle reused between calls.

## Why it needs to be done

This is a repeated connection-setup cost on every knowledge search or write, not a
correctness issue — every call is independently stateless and testable, so this is
purely an efficiency question, not a coupling or global-state smell.

**Inference, not confirmed in docs:** the lack of caching may be a deliberate,
conservative choice related to the fixed-Ollama-embedding-dimension checks
(avoiding a stale connection/handle across a dimension or model change), rather
than an oversight. Worth confirming with whoever wrote `store.py` before assuming
it's safe to add caching.

## Next step

Not yet a concrete fix. Before touching this:
1. Confirm whether the no-caching behavior is intentional (see inference above).
2. If not intentional, measure whether reconnect overhead is actually material at
   real knowledge-base query volumes before investing in a cached-connection
   change — this is a "nice to have" efficiency item, not a correctness fix.

## What happens if we do nothing

No functional impact. Some avoidable latency on every knowledge operation, bounded
by however expensive a LanceDB embedded-mode connection actually is to open (not
independently measured in this review).
