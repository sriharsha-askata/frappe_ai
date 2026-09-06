# Error-truncation limit is duplicated across five files

**Type:** Improvement (consistency)
**Severity:** Medium
**Status:** Open — approved, not yet implemented
**Source:** Architecture review, 2026-09-06 (Finding M-2)

---

## Description

Tool/provider error messages are truncated to a fixed character limit before being
returned to the model or persisted, but the limit is defined independently in five
places rather than as a shared constant:

- `frappe_ai/api/dispatch.py:_ERROR_LIMIT = 500`
- `frappe_ai/tools/builtins.py:_ERROR_LIMIT = 300`
- `frappe_ai/knowledge/ingest.py:92` — inline `[:500]`
- `frappe_ai/doctype/ai_knowledge_source/ai_knowledge_source.py:194,237` — inline
  `[:500]` (×2)
- `frappe_ai/service/frappe_client.py:238` — inline `[:500]`

## Why it needs to be done

Harmless today — the values mostly agree — but a future policy change ("keep more
error context for debugging," say) requires finding and updating five call sites
instead of one, and it's easy to miss one and reintroduce the inconsistency in a
different form. `tools/builtins.py`'s divergent 300-char limit already shows this
drift is real, not just theoretical.

## Fix

Add one shared helper, e.g. `frappe_ai/utils/errors.py:truncate_error(message,
limit=500)`, and replace the five call sites above with calls to it.

**Decide explicitly, don't silently change:** `tools/builtins.py` currently uses
300, not 500. Pick one of:
- Unify everything to 500 (the majority value), accepting that builtin tool
  errors will now carry slightly more context than before, or
- Keep 300 as an explicit override at that call site
  (`truncate_error(message, limit=300)`), preserving current behavior exactly.

Do not let the unification change `builtins.py`'s behavior by accident.

## Trade-offs

Trivial complexity, near-zero risk either way.

## Migration impact

None to schema or API contracts. Existing tests asserting truncated error length
should still pass — verify against whichever limit-per-callsite decision is made
above.

## Verification

Existing tests covering truncated error length pass unchanged (or updated
deliberately, per the 300-vs-500 decision).
