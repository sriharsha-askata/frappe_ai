# 005 - Frontend Contract

Audience: frontend apps built on top of `frappe_ai`, especially the standalone SPA at
`/app/frappe-ai`.

This document defines the app-facing contract. Clients should not depend on Frappe Desk
internals, `frappe.client`, raw DocType forms, or the old panel shell.

## 1. Positioning

`frappe_ai` is a backend product with:

- a same-origin JSON API under `frappe_ai.api.frontend.*`
- a bearer-token SSE run stream served by the FastAPI service
- an independent SPA frontend that can be hosted in Frappe Desk through thin adapters

Frappe still owns auth, permissions, persistence, configuration, and attachment staging.
Frontend clients consume those capabilities through the documented API only.

## 2. Auth Model

### JSON endpoints

- Transport: same-origin `GET` or `POST` to `/api/method/frappe_ai.api.frontend.<method>`
- Auth: logged-in Frappe session cookie
- CSRF: required for `POST`, using `X-Frappe-CSRF-Token`

### Stream endpoint

- Transport: `POST <stream_url>`
- Auth: `Authorization: Bearer <token>`
- Token source: `start_run` or `resume_run`
- Token scope: one run, short-lived

## 3. Host Adapters

The frontend is split into three layers:

1. Core app: shared state, transcript rendering, composer, activity timeline, feedback,
   session browsing, agent/model controls.
2. Transport adapter: `frontend/src/api/*` speaks the documented JSON + SSE contract.
3. Host adapter: page/panel mount code only.

Current host adapters:

- Desk panel: `frontend/src/hosts/deskPanel.tsx`
- Standalone page: `frontend/src/hosts/frappePage.tsx`

Only the host adapter may read or write host-specific persistence such as panel state or
URL session selection.

## 4. JSON Endpoints

All responses below are the normalized `message` payload returned by Frappe's method API.

### `GET frappe_ai.api.frontend.bootstrap`

Purpose: initial SPA bootstrap.

Returns:

```json
{
  "user": { "name": "user@example.com", "full_name": "Example User" },
  "agents": [{ "name": "Support Agent", "title": "Support Agent" }],
  "models": [{ "name": "GPT-4o Mini", "title": "GPT-4o Mini" }],
  "recent_sessions": [
    {
      "name": "AIS-0001",
      "title": "Renewal help",
      "modified": "2026-08-11 09:00:00",
      "agent": "Support Agent",
      "model": "GPT-4o Mini",
      "source": "Manual"
    }
  ],
  "supported_file_types": [".pdf", ".txt"],
  "capabilities": {
    "standalone_page": true,
    "panel": true,
    "custom_frontend": true,
    "stream_transport": "fastapi_bearer_sse"
  }
}
```

### `GET frappe_ai.api.frontend.sessions`

Query params:

- `query?: string`
- `limit?: number` with server clamp `1..100`

Returns:

```json
{
  "sessions": [
    {
      "name": "AIS-0001",
      "title": "Renewal help",
      "modified": "2026-08-11 09:00:00",
      "agent": "Support Agent",
      "model": "GPT-4o Mini",
      "source": "Manual"
    }
  ]
}
```

Only the current user's sessions are returned.

### `GET frappe_ai.api.frontend.session_detail`

Query params:

- `session: string`

Returns:

```json
{
  "session": {
    "name": "AIS-0001",
    "title": "Renewal help",
    "agent": "Support Agent",
    "model": "GPT-4o Mini",
    "source": "Manual",
    "modified": "2026-08-11 09:00:00"
  },
  "messages": [
    {
      "name": "AISM-0001",
      "role": "user",
      "content": "Help me renew this",
      "run": "AIR-0001",
      "tool_call_id": null,
      "tool_calls": []
    }
  ],
  "attachments": [
    {
      "name": "AISA-0001",
      "run": "AIR-0001",
      "file": "FILE-0001",
      "file_name": "renewal.pdf",
      "file_size": 1024,
      "mode": "Upload"
    }
  ],
  "paused_run": {
    "run": "AIR-0001",
    "questions": [
      {
        "key": "call_1",
        "name": "write",
        "prompt": "Approve this tool call?",
        "arguments": { "doctype": "Task" }
      }
    ]
  },
  "feedback": [
    { "run": "AIR-0001", "rating": "Up", "comment": "Good answer" }
  ]
}
```

### `POST frappe_ai.api.frontend.start_run`

Body:

```json
{
  "input": "Help me renew this",
  "agent": "Support Agent",
  "session": null,
  "model": "GPT-4o Mini",
  "attachments": ["FILE-0001"]
}
```

Returns:

```json
{
  "run": "AIR-0001",
  "session": "AIS-0001",
  "token": "eyJ...",
  "stream_url": "http://127.0.0.1:8000/chat/stream",
  "expires_in": 900
}
```

Important: assistant output does not arrive in this response. Clients must open the SSE
stream using `stream_url` and `token`.

### `POST frappe_ai.api.frontend.resume_run`

Body:

```json
{
  "run": "AIR-0001",
  "answers": {
    "call_1": "Approve",
    "call_2": "Use the open status only."
  }
}
```

Returns the same stream bootstrap shape as `start_run`.

### `POST frappe_ai.api.frontend.stop_run`

Body:

```json
{ "run": "AIR-0001" }
```

Returns:

```json
{ "status": "Failed" }
```

### `POST frappe_ai.api.frontend.recover_session`

Body:

```json
{ "session": "AIS-0001" }
```

Returns:

```json
{ "recovered": 1 }
```

Used when a client reloads a session and needs to clear abandoned running state.

### `POST frappe_ai.api.frontend.submit_feedback`

Body:

```json
{
  "run": "AIR-0001",
  "rating": "Up",
  "comment": "Good answer"
}
```

Returns:

```json
{ "rating": "Up" }
```

Allowed ratings: `"Up"`, `"Down"`, `"None"`.

### `POST frappe_ai.api.frontend.upload_attachment`

Transport: `multipart/form-data`

Fields:

- `file`

Returns:

```json
{
  "attachment": {
    "file": "FILE-0001",
    "file_name": "renewal.pdf",
    "file_size": 1024
  }
}
```

This is a staged attachment object for later use in `start_run`.

### `GET frappe_ai.api.frontend.agent_tools`

Query params:

- `agent: string`

Returns:

```json
{
  "tools": {
    "read": { "requires_confirmation": false },
    "write": { "requires_confirmation": true }
  }
}
```

### `GET frappe_ai.api.frontend.run_feedback`

Query params:

- `run: string`

Returns:

```json
{
  "run": "AIR-0001",
  "rating": "Up",
  "comment": "Good answer"
}
```

## 5. Stream Contract

After `start_run` or `resume_run`, the client opens:

```http
POST <stream_url>
Authorization: Bearer <token>
Content-Type: application/json
```

Resume requests send:

```json
{
  "answers": {
    "call_1": "Approve"
  }
}
```

New runs may send `{}`.

The server emits SSE `data:` frames containing JSON objects. The frontend normalizes them
into the following event shapes:

### `run_started`

```json
{
  "type": "run_started",
  "run": "AIR-0001",
  "session": "AIS-0001"
}
```

Client behavior:

- mark the active run/session
- keep the assistant message pending

### `text`

```json
{
  "type": "text",
  "content": "Here is the next chunk."
}
```

Client behavior:

- append `content` to the current assistant transcript

### `tool_started`

```json
{
  "type": "tool_started",
  "id": "call_1",
  "name": "read",
  "arguments": { "doctype": "Task" }
}
```

Client behavior:

- append or update a tool activity row
- apply per-tool metadata from `agent_tools`

### `tool_ended`

```json
{
  "type": "tool_ended",
  "id": "call_1",
  "result": "{\"status\":\"approved\"}"
}
```

Client behavior:

- complete the matching tool activity row
- derive approval status if the tool required confirmation

### `done`

```json
{
  "type": "done",
  "status": "Completed"
}
```

Paused example:

```json
{
  "type": "done",
  "status": "Paused",
  "questions": [
    {
      "key": "call_1",
      "name": "write",
      "prompt": "Approve this tool call?",
      "arguments": { "doctype": "Task" }
    }
  ]
}
```

Client behavior:

- mark the assistant message no longer pending
- if `status == "Paused"`, render confirmation questions and wait for `resume_run`
- otherwise refresh history and allow the next turn

### `error`

```json
{
  "type": "error",
  "message": "Something failed."
}
```

Client behavior:

- append the error to the active assistant message
- clear pending state

## 6. Failure Contract

### JSON methods

- Transport failures use HTTP status codes.
- Frappe method failures may also arrive as `200` with `exc` or `_server_messages`.
- Frontend clients should extract a user-safe message from `_server_messages` first.

Current frontend helper order:

1. first server message from `_server_messages`
2. `exception`
3. `_error_message`
4. `message`

### Stream bootstrap failures

- `start_run` and `resume_run` can fail before any SSE stream is opened.
- The client should show the returned message directly when it comes from Frappe
  validation or permission checks.

### Stream failures

- Non-2xx stream responses may return JSON with `detail`.
- The client should surface `detail` first, then fall back to the generic server-message
  extraction order above.

## 7. Example Flows

### Start a new conversation

1. `GET bootstrap`
2. user submits prompt
3. `POST start_run`
4. `POST stream_url` with bearer token
5. consume `run_started`, `text`, `tool_*`, `done`

### Resume a paused confirmation

1. `GET session_detail`
2. render `paused_run.questions`
3. user answers prompts
4. `POST resume_run`
5. `POST stream_url` with bearer token and `{answers}`

### Upload an attachment

1. `POST upload_attachment` with multipart `file`
2. retain returned staged `attachment.file`
3. pass that file id in `start_run.attachments`

### Restore a session after reload

1. `GET bootstrap`
2. select current session via host adapter state
3. `POST recover_session`
4. `GET session_detail`
5. continue from restored transcript
