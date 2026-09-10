# Error Log records full tool arguments and prompt context

**Type:** Bug / Privacy gap
**Severity:** Medium
**Status:** Open
**Source:** Production readiness review, 2026-09-10 (Finding F-13)

---

## Description

Two paths write user and business data into Frappe's `Error Log`:

- `frappe_ai/api/dispatch.py:dispatch_plugin_tool` logs the complete
  `tool_arguments` dict on any tool failure.
- `frappe_ai/triggers/triggers.py:fire_manual_trigger` logs the full `context`
  dict on **every** call — not only on failure — and again on completion.

The dispatch logging is deliberate and its rationale is sound: a tool's own
`ValidationError` reads identically whether the data was missing or the model
passed a malformed argument, and the arguments are the only way to tell the two
apart. The problem is the destination, not the intent.

## Why it needs to be done

`Error Log` is not a private channel. It is readable by System Manager and
commonly granted more widely for support purposes, and it is included in
site backups and error report emails.

Tool arguments are the agent's payload: document contents, customer names,
pricing, whatever the user asked about. Manual trigger context is worse, because
it is logged unconditionally on the success path — a trigger firing normally at
volume writes a continuous stream of business data into a support-readable table
with no retention policy.

This is a compliance and data-handling concern rather than an exploitable
vulnerability, which is why it is Medium rather than High. But it accumulates
silently, and it is far cheaper to fix before a production corpus exists than
after.

## Fix

Three changes, in order of value:

1. **Drop the unconditional success-path logging** in `fire_manual_trigger`.
   Logging a normal completion to `Error Log` is a category error regardless of
   content; the run is already recorded in `AI Run`.
2. **Redact by default on the failure paths.** Log argument *keys*, types and
   sizes rather than values — enough to distinguish "model sent the wrong shape"
   from "data genuinely missing", which is the diagnostic need the current code
   documents.
3. **Gate full-value logging behind a setting** (e.g. an `AI Settings` debug flag,
   default off) for the cases where redacted output is insufficient.

The run's own record is the right home for anything that must be retained.

## Trade-offs

Redaction makes some failures harder to diagnose from logs alone. Point 3 exists
to recover that when needed, deliberately and temporarily, rather than logging
everything permanently by default.

## Verification

- A failing `dispatch_plugin_tool` call produces an Error Log entry containing
  argument names and types but no argument values.
- A successful manual trigger produces no Error Log entry at all.
- With the debug flag enabled, full arguments are logged again.
