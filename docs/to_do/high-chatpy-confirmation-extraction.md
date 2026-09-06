# Extract confirmation-handling logic out of `chat.py`

**Type:** Improvement (refactor)
**Severity:** High
**Status:** Open — approved, not yet implemented
**Source:** Architecture review, 2026-09-06 (Finding H-2 / Decision 2)

---

## Description

`frappe_ai/service/routes/chat.py` is 860 lines and ~20 module-level functions:
SSE translation, the confirmation pause/resume state machine, retry-on-provider-
failure, and Agno-event mapping all live in one file. Roughly 300 of those lines
are the confirmation-handling logic specifically:

- `_dispatch_approved`
- `_denied_result`
- `_messages_excluding_pending`
- `_pending_from_tool_execution`
- `_reconstructed_tool_call_message`
- `_confirm_prompt`
- `_approved_result_messages`
- `_split_answers`
- `_redirects`

These are interleaved with SSE framing and retry logic that has nothing to do with
confirmation semantics.

## Why it needs to be done

This is not a hypothetical risk area — it is the app's **only documented
multi-bug incident**. `docs/learnings.md` records three sequential bugs found only
by live-testing all three confirmation answer types (Approve/Deny/free-text
redirect): a poisoned transcript that made the model hallucinate a fake tool
result, an approval that silently didn't dispatch anything, and a denial that
dropped its own audit record. All three fixes are confirmed present in current
code, but they landed one at a time, in place, in an already-crowded file.

The highest-incident-history code in the app is currently the hardest to read in
isolation, because it's interleaved with concerns (streaming, retry) that a future
engineer touching pause/resume doesn't need to reason about at the same time.
Leaving it in place means the next confirmation-related fix lands in the same
crowded file, continuing the trend.

## Fix

Extract the nine functions above into a new `service/routes/confirmation.py`.
This is pure code motion — they are already free functions, not methods on a
shared object — `chat.py` imports and calls them exactly as it does today. No
behavior change.

Note: `PendingConfirmation` currently lives in `service/builder.py`, not
`chat.py` — the new module imports it from there rather than duplicating the
class.

## Trade-offs

One more file to navigate. Otherwise none — this does not change what the code
does, only where it lives.

## Migration impact

`test_chat_route.py` may patch these functions by module path
(`unittest.mock.patch`) — if so, those patch targets need updating to the new
module path. Every existing assertion should pass unmodified once import paths
are updated; run the full `test_chat_route.py` suite after extraction and confirm
no regressions.

## Prerequisite

Confirm the existing `test_chat_route.py`/`test_builder.py` suites currently pass,
as a baseline, before starting the extraction (this is Step 0 of the broader
refactoring plan — see the architecture review's §18).

## Verification

Full `test_chat_route.py` suite passes unchanged after extraction, with only
import-path updates to mock targets if any exist.
