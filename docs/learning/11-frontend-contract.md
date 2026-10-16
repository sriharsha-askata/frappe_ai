# Lesson 11 - The frontend contract

Files: `api/frontend.py` (JSON API), `frontend/src/api/{client,stream}.ts`, `frontend/src/state/{runEvents,store,types}.ts`, `frontend/src/hosts/{deskPanel,frappePage}.tsx`, built bundle in `public/frappe_ai_panel/`. Spec: `docs/specifications/005-frontend-contract.md`.

## Why it exists
Everything in Lessons 1-10 happens behind two doors that a client must use correctly: a same-origin JSON API (session cookie + CSRF) for everything except streaming, and a bearer-token SSE stream served by FastAPI. `frontend.py` is a thin, stable wrapper over `api/api.py`; it also shapes data for the UI.

## The endpoints (`frappe_ai.api.frontend.*`, all `@frappe.whitelist()`)
`bootstrap`, `sessions`, `session_detail`, `start_run`, `resume_run`, `stop_run`, `recover_session`, `submit_feedback`, `upload_attachment`, `agent_tools`, `run_feedback`. The run-control ones delegate: `start_run` -> `api.start_run`; `resume_run(run, answers)` -> `api.resume_run(run_name=run, ...)`; `stop_run` -> `api.stop_run`. Session-reading endpoints check ownership (`assert_session_owner`); `sessions` lists only the caller's non-Trigger sessions (title search with LIKE-escaping, limit clamped 1-100); `upload_attachment` rejects Guest, saves a private File and returns the staged attachment.

## Two-step run protocol (`openRun` in `stream.ts`)
1. `POST /api/method/frappe_ai.api.frontend.start_run` with the CSRF header and cookie -> `{run, session, token, stream_url, expires_in}`.
2. `POST stream_url` with `Authorization: Bearer <token>` and body `{}` (or `{answers}` for resume).
3. `consumeStream` reads the response body, splits on blank lines, takes the `data: ` line of each block, JSON-parses and `normalizeEvent`s it.

## Events and the state machine (`runEvents.ts::applyRunEvent`)
- `run_started` -> remember the run/session.
- `text` -> append `content` to the assistant message.
- `tool_started` -> add an execution row (status running).
- `tool_ended` -> store the result and derive status/approval from it.
- `done` -> stop pending; if `status == "Paused"`, show confirmation questions and mark matching executions `awaiting_confirmation`.
- `error` -> show the message and mark the run Failed.
The reducer is a pure function over store state; host adapters (`deskPanel`, `frappePage`) only mount the app and persist host-specific things (panel state, URL selection).

## Resume
UI collects `answers` (`Approve` / `Deny` / free text per question key) -> `resumeRun` posts `{run, answers}` to Frappe (which records approvals, Lesson 6) and then streams with `{answers}`.

## Things to know (verified by reading the code, not run)
- The spec `005-frontend-contract.md` is out of date versus `frontend.py`: the real `bootstrap` returns nested `agent` / `session` / `execution` / `composer` / `capabilities` objects, `sessions` returns `{"session": {"history": [...]}}`, and `agent_tools` returns `{"tools": {"count", "summaries"}, "mcp_connections"}`. Trust the code (and `types.ts`) over the spec until the spec is updated.
- As Lesson 4 noted, the service flushes frames only after the turn ends, so the UI sees `text` and `tool_*` events in a burst at the end.
- If the stream closes without a `done` or `error` frame, `consumeStream` simply ends; `recover_session` is the cleanup path after a reload.

## Checkpoint questions
1. Why two different authentication mechanisms (cookie+CSRF vs bearer token)?
2. Trace the events a client sees when a tool needs approval, and what it must send to continue.
3. Why is `applyRunEvent` a pure function and what does that buy for testing?
4. What would break if a client reused an expired token for `stream_url`?
