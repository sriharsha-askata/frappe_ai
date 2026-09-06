# No correlation ID across Frappe and FastAPI logs

**Type:** Improvement (observability)
**Severity:** Medium
**Status:** Open — approved, not yet implemented
**Source:** Architecture review, 2026-09-06 (Finding M-1 / Decision 4)

---

## Description

No app-generated id ties together an `AI Run` record, the FastAPI process's logs
for that run, and the Frappe-side dispatch calls it triggers. The only ID-like
field present anywhere in the traced code is the LLM provider's own `request_id`,
passed through `lib/model.py`'s `normalize_provider_error()`.

`logging.getLogger` is used in exactly 4 non-test files across the whole app;
zero `frappe.logger` calls exist in non-test backend source.

## Why it needs to be done

[ADR 0003](../decisions/0003-tools-execute-in-frappe.md) — the decision that
created the two-process split — names "two-process debugging... correlation ids
are mandatory" as an explicit, expected consequence of that design. No such id was
ever implemented. Reconstructing "why did this specific run fail" across two
processes' logs today requires manually matching timestamps and the `AI Run` name
by eye — workable at low volume, painful the first time it's actually needed under
incident pressure.

This is the single highest debuggability-per-effort item found in the review:
it requires no architectural change, just threading one generated id through
existing log call sites and the dispatch HTTP headers.

## Fix

Reuse the existing `AI Run` name as the correlation id — it's already unique and
already the natural join key across both processes, so no new id-generation scheme
is needed.

- Add it as a header (e.g. `X-Frappe-AI-Run`) on every `FrappeClient` call
  (`service/frappe_client.py`).
- Include it in every existing `logging.getLogger` call site
  (`service/builder.py`, `service/routes/chat.py`, `service/main.py`,
  `frappe_ai/doctype/ai_knowledge_source/ai_knowledge_source.py`), via a
  `logging.LoggerAdapter` or an explicit kwarg.
- Log it at the Frappe-side dispatch boundary (`api/dispatch.py`) too, so both
  sides of any single call are traceable by the same id.

## Trade-offs

Mechanical but touches several files. No behavior change — purely additive to
existing log calls and one new header.

## Migration impact

None to data or schema.

## Verification

Run one test conversation end to end and confirm the same id appears in both
Frappe's and FastAPI's log output for it.
