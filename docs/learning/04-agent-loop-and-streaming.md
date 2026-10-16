# Lesson 4 - The agent loop and streaming

Files: `service/routes/chat.py` (orchestrator), `agno_events.py` (runs Agno, buffers events),
`transcript.py` (stored messages <-> Agno messages), plus the helpers in `confirmation.py`.

## Concepts
- **Message list = the context window.** Each model call receives the whole list: system, user, assistant, tool messages. A tool call is an assistant message with `tool_calls`; its result is a `tool` message with the same `tool_call_id`. APIs reject a tool result with no matching request.
- **Agno events** with `stream=True, stream_events=True`: run started, model request started/completed (with token counts), content chunks, tool call started/completed, run error, and (with `yield_run_output=True`) a final `RunOutput`.
- **Fallback model**: retry the same turn on a different model after a provider failure.

## Flow of `stream_chat(run, user, session, client, answers)`
1. Yield `run_started`.
2. `_split_answers`: `Approve` ids, `Deny` ids; anything else is redirect feedback.
3. `builder.build` (Lesson 3) returns the agent and config; `config["questions"]` are the prior pause's questions.
4. **Deny**: persist a denial result (no model call), yield `done`, stop (`_denied_result`).
5. **Approve**: `_dispatch_approved` executes each approved call directly in Frappe (the model is not asked to re-request it), yielding `tool_started`/`tool_ended`.
6. `_to_agno_messages` rebuilds the history; for approved and redirected calls it reconstructs the assistant `tool_calls` message plus the tool result, because the pending request was deliberately not stored.
7. `_run_agent_turn` runs `agent.arun(...)` to completion.
8. **Fallback**: if there was a model error, nothing pending and no tool completed, fetch the fallback model config and rerun the whole turn on a new `Agent`. Safe only because no side effect has happened yet.
9. Flush frames; on error call `fail_run` and yield `error`; otherwise `_build_result` -> `persist_run_result` -> yield `done`.
10. `GeneratorExit` (client disconnected) -> `fail_run("Stream interrupted")`, re-raise.

## `_run_agent_turn`
- Consumes events and turns them into SSE frames (`text`, `tool_started`, `tool_ended`), counts model calls and logs timings and tokens, records `tool_completed`, and spots pending confirmations: a failed tool whose result starts with `PENDING_CONFIRMATION_MARKER` becomes a `PendingConfirmation` instead of a `tool_ended` frame.
- Wraps the model's `ainvoke_stream` to convert timeouts into a clear `ModelProviderError` (504) mentioning `AI Settings.stream_timeout`.
- Attaches a logging handler to Agno's logger so Agno errors reach Frappe's Error Log, removed in `finally`.

## Transcript rules (`transcript.py`)
- `_repair_orphaned_tool_results` inserts a stub assistant request before any tool result lacking one.
- `_messages` drops the system row and truncates stored tool output to 8000 chars.
- `_messages_excluding_pending` removes the marker tool response and pure-pending assistant requests so a fake result is never replayed.
- `_denied_result` completes the run with `{"status": "denied"}` tool rows.

## Important observation (verify on a bench)
`_run_agent_turn` appends frames to `attempt.frames` and `stream_chat` yields them only after the whole turn finishes. So the client receives `text`/`tool_*` frames in a burst at the end, not token by token. This is what makes the fallback retry safe (discarded frames never reached the client), but it costs live streaming and means an idle connection during the whole turn (SSE heartbeats are listed as missing in the production-readiness docs).

## Other notes
- The fallback `Agent` is built without `tool_call_limit`, unlike the primary agent.
- Usage (prompt/completion/total tokens) is collected from Agno metrics and persisted with the result.
- Error handling is broad: build/Frappe errors, generator exit and generic exceptions each fail the run if not yet persisted.

## Checkpoint questions
1. Why buffer frames instead of yielding as they arrive, and what is the user-visible cost?
2. After an Approve, why does the service dispatch the call itself rather than waiting for the model to ask again?
3. Why is a fallback retry forbidden once any tool call has completed?
4. What would happen to a resume if `_messages_excluding_pending` did not remove the marker tool response?
