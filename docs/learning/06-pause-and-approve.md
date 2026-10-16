# Lesson 6 - Pause and approve (human in the loop)

Files: `doctype/ai_run/ai_run.py` (run state, approvals), `api/api.py` (`resume_run`, `stop_run`, `persist_run_result`, `fail_run`), `service/routes/confirmation.py` and `agno_events.py` (Lessons 3-4).

## Concepts
- **Human in the loop**: a person approves a risky action before it runs.
- **Run lifecycle**: `Running` -> `Paused` (needs answers) -> `Running` (resume) -> `Completed` | `Failed`. A run is a series of **segments**; each pause ends one and each resume starts the next.
- **State lives in Frappe**: a pause can last minutes or days, so nothing is held in the service.

## The full cycle
1. **Pause (service).** A tool needing approval raises `PendingConfirmation` (Lesson 3); `_run_agent_turn` collects them; `_build_result` returns `status: "Paused"`, `questions` (key = call id, name, arguments, prompt), and a transcript without the pending call (`_messages_excluding_pending`).
2. **Persist (Frappe).** `persist_run_result` locks the run row, calls `AIRun.apply_result`: sets status, adds iterations, stores `questions` only when `Paused`, merges token usage, appends only new messages to the session (`_new_messages_for_session`). `validate` enforces: Paused must have questions; Failed must have an error.
3. **Ask (UI).** `session_detail.paused_run.questions` renders Approve / Deny / redirect text per question.
4. **Answer (Frappe, user-authenticated).** `resume_run`: parses answers; run must be `Paused` and owned by the caller; `record_approvals` stores, per approved call id, `{tool, args_hash}` where `args_hash` = SHA-256 of the canonical JSON of the arguments the user was shown; resets `segment_started_at`; mints a new token.
5. **Resume (service).** Browser opens the stream with `{answers}`. `stream_chat`: Deny -> end the run; Approve -> `_dispatch_approved` runs the call using the stored question's arguments; redirect -> a synthetic tool result `{"status": "redirect", "user_feedback": ..., "instruction": ...}` so the model adapts.
6. **Enforce (Frappe).** `dispatch_plugin_tool` calls `consume_approval`: locks the run, requires a stored approval for that call id with the same tool and argument hash, removes it (single use).

## Finished runs are immutable
`TERMINAL_STATUSES = (Completed, Failed)`. `apply_result` and `mark_failed` raise `RunAlreadyFinished`; `persist_run_result`/`fail_run` catch it and return `{"status": ..., "ignored": True}`; `stop_run` locks the row, tries `mark_failed("Stopped by user.")`, ignores the exception and returns the real status. So a user's Stop stays a Stop even if the model finishes later.

## Example (hypothetical)
Model wants `delete_invoice(name=INV-1)` -> run `Paused`, one question. User approves -> `approvals = {"call_1": {"tool": "delete_invoice", "args_hash": "ab12..."}}`. Resume dispatches with the stored arguments, `consume_approval` matches and deletes the entry; a replay of `call_1` or a call with other arguments is refused.

## Production notes
- Handled: approvals recorded only by the logged-in user, bound to tool and exact arguments, single use; terminal-state immutability; row locks on every write path; runtime budget excludes human wait.
- Partial: `persist_run_result` runs as Administrator after a secret check (trusts the service for transcript content); redirect feedback is free text fed to the model (prompt-injection surface, but it comes from the run owner).
- Missing: no expiry for a Paused run or its approvals; no notification when a run is waiting.

## Checkpoint questions
1. Why is the approval recorded in `resume_run` rather than in the service?
2. What does hashing the arguments prevent that matching only the tool name would not?
3. A user clicks Stop while the model is finishing. Walk through what each callback does.
4. Why does `_new_messages_for_session` skip existing messages instead of saving the whole transcript?
