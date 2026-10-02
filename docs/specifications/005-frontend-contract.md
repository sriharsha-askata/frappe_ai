# 005 — Frontend contract

What a user interface needs to know to talk to `frappe_ai`. The bundled React app (`frontend/`) follows this contract, and you can write another client against it. Do not depend on Desk internals, `frappe.client`, or raw DocType forms.

## 1. The two kinds of calls

| Kind | Used for | How |
|---|---|---|
| **JSON endpoints** (Frappe) | Loading agents and history, starting and controlling runs, feedback, uploads | `GET`/`POST /api/method/frappe_ai.api.frontend.<name>`, logged-in session cookie. `POST` needs the header `X-Frappe-CSRF-Token` |
| **Run stream** (FastAPI service) | The answer, as it is written | `POST <stream_url>` with `Authorization: Bearer <token>`; response is `text/event-stream` |

Frappe still owns login, permissions, storage and uploads. The service only streams.

All JSON responses below are the `message` part of Frappe's usual `{"message": …}` wrapper. Errors come back the Frappe way (`exc`, `_server_messages`).

## 2. The code that implements it

| Layer | Files |
|---|---|
| Server API | `frappe_ai/api/frontend.py` (shapes the data for the UI), `frappe_ai/api/api.py` (the underlying logic) |
| Stream | `frappe_ai/service/main.py`, `service/routes/chat.py` |
| Client transport | `frontend/src/api/client.ts` (JSON), `frontend/src/api/stream.ts` (stream) |
| Hosts (mount code only) | `frontend/src/hosts/deskPanel.tsx` (slide-in panel), `frontend/src/hosts/frappePage.tsx` (full page `/app/frappe-ai`) |

Only a host may keep host-specific state such as panel size or the selected session in the URL.

## 3. JSON endpoints

All take the logged-in user into account; sessions and runs are limited to their owner.

### `GET bootstrap`

Everything the app needs at start-up.

```json
{
  "user":   { "name": "user@example.com", "full_name": "Example User" },
  "agent":  { "selected": "Frappe AI",
              "items":  [ { "id": "…", "name": "…", "title": "…",
                            "readiness": { "state": "ready", "label": "Ready" },
                            "model": { "name": "…", "title": "…" },
                            "tools": { "count": 3, "summaries": [ … ] },
                            "mcp_connections": [ … ],
                            "prompt_summary": "…", "output_summary": "Markdown enabled",
                            "configure_action": { "label": "Configure agent", "target": "/app/ai-agent/…" } } ],
              "models": [ { "name": "…", "title": "…" } ] },
  "session": { "current": null, "history": [ { "id": "…", "name": "…", "title": "…", "preview": "…",
                                               "modified": "…", "agent": "…", "model": "…", "source": "Manual" } ] },
  "execution": { "current_run": null, "transcript": [], "paused_run": null, "feedback": [] },
  "composer": { "supported_file_types": [".pdf", ".txt"] },
  "capabilities": { "standalone_page": true, "panel": true, "custom_frontend": true,
                    "stream_transport": "fastapi_bearer_sse" }
}
```

Only enabled agents and models (up to 50) are listed. `readiness.state` is `needs_model` when an agent has no model. The default selection is the agent named "Frappe AI" if it exists.

### `GET sessions` — `query?`, `limit?` (1–100, default 20)

Returns `{ "session": { "history": [ …session rows… ] } }` for the current user's sessions, newest first, excluding sessions started by triggers. `query` matches the title.

### `GET session_detail` — `session`

Loads one conversation to show or restore it.

```json
{
  "agent":   { "selected": "<agent name>" },
  "session": { "current": { "id": "…", "title": "…", "agent": "…", "model": "…", "source": "…", "modified": "…" } },
  "execution": {
    "current_run": { "run": "…", "status": "Running|Paused", "started_at": "…", "updated_at": "…", "error": "" },
    "transcript": [ … ],
    "paused_run": { "run": "…", "questions": [ { "key": "call_1", "name": "…", "arguments": { }, "prompt": "…" } ] },
    "feedback":   [ { "run": "…", "rating": "Up", "comment": "…" } ],
    "attachments": [ { "id": "…", "run": "…", "file": "…", "file_name": "…", "file_size": 1024, "mode": "Inline|Retrieval" } ]
  }
}
```

A transcript entry is either a user message (`role: "user"`, `content`, `run`, `attachments`) or an assistant message (`role: "assistant"`, `content`, `run`, `questions` for a paused run, `feedback`, and `executions`). Each execution describes one tool call: `id`, `kind` (`tool` or `mcp_tool`), `tool_name`, `display_title`, `status` (`running`, `completed`, `error`, `awaiting_confirmation`), `input_summary`, `result_summary`, `raw_input`, `raw_output`, `error`, and `approval_status` (`approved`, `denied`, `redirected`, or null).

### `POST start_run`

```json
{ "input": "Help me renew this", "agent": "Support Agent", "session": null,
  "model": null, "attachments": ["<File name>"] }
```

`agent` is required for a new session; with `session` the session's agent is reused. `model` switches the session's model unless a run is Paused or Running. Returns:

```json
{ "run": "…", "session": "…", "token": "…", "stream_url": "http://127.0.0.1:8001/stream/<run>", "expires_in": 300 }
```

The answer does **not** come in this response. Open the stream with `stream_url` and `token`.

### `POST resume_run` — `run`, `answers`

Continue a Paused run. `answers` maps each pending call id (the question `key`) to `"Approve"`, `"Deny"`, or free text (feedback for the model). Returns the same shape as `start_run`. When opening the stream for a resume, send the same `answers` in the JSON body. Frappe records the approvals when it receives this call, so approving is a server-side fact, not only a client message.

### `POST stop_run` — `run`

Stop a run: terminates a Paused run, or finishes a Running run whose stream the client abandoned. Returns `{ "status": … }`. A late result from the service is ignored.

### `POST recover_session` — `session`

Fails any run still marked Running for that session (use on reload after a lost stream). Returns `{ "recovered": <count> }`.

### `POST submit_feedback` — `run`, `rating`, `comment?`

`rating` is `Up`, `Down`, or `None` (clears it). A `Down` with a comment is also stored as agent memory. Returns `{ "rating": … }`.

### `GET run_feedback` — `run`

Returns `{ "run", "rating", "comment" }`.

### `POST upload_attachment` (multipart form, field `file`)

Saves a private file and returns `{ "attachment": { "file", "file_name", "file_size" } }`. Pass the `file` value in `start_run`'s `attachments`. Allowed types are listed in `bootstrap.composer.supported_file_types`.

### `GET agent_tools` — `agent`

Returns `{ "tools": { "count", "summaries": […] }, "mcp_connections": […] }` so the UI can show what an agent can do and which tools need approval.

## 4. The stream

`POST <stream_url>` with `Authorization: Bearer <token>`, `Content-Type: application/json`, and a body of `{}` (or `{"answers": {…}}` for a resume). The response is Server-Sent Events: frames of `event: <name>` and `data: <json>`, separated by a blank line. The JSON always includes `type` equal to the event name.

| Event | Fields | Notes |
|---|---|---|
| `run_started` | `run`, `session` | First frame |
| `text` | `content` | Append to the current answer |
| `tool_started` | `id`, `name`, `arguments` | A tool call began |
| `tool_ended` | `id`, `name`, `result` | The tool call returned |
| `done` | `status`, `iterations`, `output`, `usage`, `questions?` | Last frame. `status` is `Completed`, or `Paused` with `questions` (one per call waiting for approval) |
| `error` | `message`, `code`, `status_code`, `retryable`, optional `diagnostics` | The run failed; last frame |

Rules for clients:

- Treat `done` or `error` as the end. The server has already saved the result.
- Do not show Paused `questions` as an error; show an approval card per question (`key` is what you send back in `resume_run`).
- If the stream breaks before `done`, call `recover_session` and reload the session.
- The bundled client uses `fetch` and reads the body as a stream, because a resume needs a JSON body (browser `EventSource` cannot send one).

## 5. Failures

| Where | What you get |
|---|---|
| JSON endpoint | Frappe's normal error response (`exc`, `_server_messages`) |
| Opening the stream | HTTP 401 (`detail`) for a missing, tampered, expired or wrong-run token; the service not running is a network error |
| During the stream | An `error` event, then the stream ends; the run is marked Failed |
| Closing the tab mid-stream | The service marks the run Failed ("Stream interrupted") |

## 6. Typical flows

**New conversation:** `bootstrap` → `start_run` → open stream → render `text` events → `done`.

**Approval:** stream ends with `done` (`Paused`) → show the cards → `resume_run` with the answers → open a new stream with the same answers → `done`.

**Attachment:** `upload_attachment` → keep the returned `file` → `start_run` with `attachments: [file]`.

**Restore after reload:** `recover_session` (fails any stuck run) → `session_detail` → render the transcript; if `paused_run` is set, show its approval cards.
