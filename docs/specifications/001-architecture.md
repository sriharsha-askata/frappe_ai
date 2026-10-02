# 001 — Architecture

How `frappe_ai` is put together, how one request flows through it, and who is allowed to do what. Read this first; the other specifications go deeper on one part each.

## 1. What the app does

`frappe_ai` adds AI agents to Frappe:

- **Chat** with an agent in the Desk, with the answer streamed as it is written.
- **Tools** the agent can call to read or change Frappe data. Risky tools pause for the user's approval.
- **Knowledge**: documents split into chunks and indexed so the agent can search them.
- **Memory**: facts an agent keeps between conversations.
- **Triggers**: start an agent when a document changes, on a schedule, or from app code.
- **Audit**: every conversation and run is stored in Frappe.

## 2. The central design choice: two programs

An agent run is slow. The model may think for many seconds and the agent may loop through several tool calls. If that waited inside a Frappe web worker, a few simultaneous runs would use up the workers and the whole Desk would slow down (roughly 10–20 concurrent runs on a default setup).

So the work is split:

| Program | Does | Does not |
|---|---|---|
| **Frappe** (port 8000) | Stores all configuration and history, checks permissions, **runs every tool**, holds API keys, runs triggers and background jobs, owns the knowledge index | Wait on the language model |
| **FastAPI service** (port 8001) | Talks to the model, runs the agent loop (using the Agno library), streams the answer to the browser | Read Frappe's database, store anything, hold credentials at rest |

```
 Browser (React panel or page)
    │ (1) normal Frappe calls: start_run, resume_run, stop_run, history…
    │ (2) one streaming request straight to the service, with a run token
    ▼                                          ▼
┌─────────────────────────────┐        ┌───────────────────────────────┐
│ Frappe                      │        │ FastAPI service               │
│  • AI DocTypes (the truth)  │ ◄───── │  • AgentBuilder: config →     │
│  • permission checks        │ (3)    │    Agno agent                 │
│  • tool dispatch            │ ─────► │  • agent loop, model calls    │
│  • result callbacks         │        │  • SSE streaming              │
│  • knowledge + memory index │        │  stateless; talks to Frappe   │
│  • triggers, scheduler      │        │  over HTTP only               │
└─────────────────────────────┘        └───────────────────────────────┘
```

(3) is plain HTTP in both directions. The service asks Frappe for the run's configuration, asks Frappe to run each tool, and sends the result back when the run ends.

The service reads one file from disk, the site's `site_config.json`, to learn the shared secret. It never opens a database connection ([ADR 0001](../decisions/0001-agno-fastapi-over-frappe-native.md), [ADR 0011](../decisions/0011-service-secret-in-site-config.md)).

## 3. Who owns what

When a question comes up while writing code, decide it with this table.

| Concern | Owner | Why |
|---|---|---|
| Configuration (agents, models, tools, knowledge bases) | Frappe | Editable in the Desk, no restart |
| API keys | Frappe (Password fields) | Handed to the service per run, never stored there |
| Permission for every data operation | Frappe | `frappe.has_permission` is the only authority |
| Running a tool against Frappe data | Frappe | It follows from permissions |
| Running model-written code | Frappe (`safe_exec`) | The sandbox lives next to the data it protects |
| Model calls and the agent loop | Service | This is the slow part |
| Streaming to the browser | Service | Long connections must not hold Frappe workers |
| Knowledge and memory indexes (LanceDB) | Frappe code | A site-local cache; writes stay behind Frappe |
| Detecting triggers (doc events, cron) | Frappe | Needs Frappe hooks and the scheduler |

**The invariant:** *the service never reads or writes Frappe's database and never keeps a credential. It orchestrates; Frappe authorizes.* If the service ran tools itself, every user would effectively act as one powerful service account. See [ADR 0003](../decisions/0003-tools-execute-in-frappe.md).

## 4. Life of a chat turn

### 4.1 Start and stream

1. **Browser → Frappe: `start_run`** (`frappe_ai/api/api.py`). Frappe finds or creates the `AI Session`, checks that the agent and model are enabled and the user may use the model, creates an `AI Run` with status *Running* and a snapshot of the agent's settings, saves the user's message, and returns `{run, session, token, stream_url, expires_in}`.
   - It does **not** check that the service is up. If the service is down the browser's next call fails, and the run is cleaned up later (see §7).
2. **Browser → Service: `POST /stream/{run}`** with `Authorization: Bearer <token>`. The service checks the token's signature, expiry, and that it is for this run (`service/auth.py`, `service/main.py`).
3. **Service → Frappe: `get_run_config`** (`api/service.py`). Frappe re-checks the run is active and belongs to the user, and returns the agent, the model's connection details, the tool list with their schemas, MCP connections, the conversation so far, and any pending questions.
4. **Service builds an agent** (`service/builder.py`) and runs it (`service/routes/chat.py`). Model output becomes `text` events.
5. **Tool call.** The model asks for a tool. The service calls Frappe (`dispatch_plugin_tool` in `api/dispatch.py`, or `dispatch_tool` for legacy `AI Tool` rows). Frappe checks the run and user, whether approval is needed, and the budgets, then runs the tool **as the user** and returns the result. The service gives it to the model and the loop continues.
6. **Finish.** The service calls `persist_run_result`. Frappe stores the messages, usage and status on the `AI Run`. The service sends a final `done` event.

### 4.2 Approval pause and resume

Tools can be marked "requires confirmation" (per tool, or per agent binding for Assistant Core tools). The first time the model asks for one, it does **not** run:

1. The service's tool wrapper raises a `PendingConfirmation` instead of calling Frappe. The run loop collects these, stops asking the model, and ends the stream with `done` and `status: "Paused"` plus a `questions` list (one entry per pending call: `key` = the tool call id, the tool name, its arguments, and a prompt).
2. Frappe saves the questions on the `AI Run` and sets it to *Paused*. The UI shows an approval card.
3. The user answers. The browser calls **`resume_run`** with `answers`, a map from call id to:
   - `"Approve"`: run that call with the arguments the user saw;
   - `"Deny"`: stop the whole run immediately;
   - any other text: sent back to the model as feedback so it can change its approach.
4. `resume_run` checks the run is *Paused* and owned by the user, **records each approval on the run** (`AI Run.approvals`: call id, tool, a hash of the arguments), starts a new active period (`segment_started_at`), and returns a fresh token.
5. The browser opens a new stream. The service runs each approved call by asking Frappe, which accepts it only if a matching approval is on record. Each approval can be used **once**.

So approval is enforced by Frappe, not just by the service ([ADR 0003](../decisions/0003-tools-execute-in-frappe.md)). Runs started by a trigger with `auto_approve` skip this check.

### 4.3 Trigger runs

`hooks.py` registers a hook on every DocType event (`doc_events["*"]`) and a 5-minute scheduler job. For a DocType-event trigger:

1. `triggers.dispatch` finds enabled triggers for that DocType and event, evaluates each trigger's condition as the trigger's `run_as` user, and enqueues `triggers.fire` after the transaction commits.
2. The background job loads the document again, re-checks the condition (the document may have changed), renders the trigger's prompt template with `{doc, now}`, creates a session and run, and starts the run through the service.

Manual triggers are started from app code with `fire_manual_trigger`. Scheduled triggers use the same `fire` path, driven by a cron expression.

Two things to know today: the background job currently reads the whole stream, so a trigger run occupies a Frappe worker until it finishes (a known gap, see [010](010-review-topics.md)); and trigger runs add a safety note to the agent's instructions telling it that text from documents is data, not commands.

## 5. Security model

### 5.1 Browser → service: run tokens

Frappe makes a token with an HMAC signature over `(run, session, user, expiry)`, using the shared secret from `site_config.json` (`frappe_ai_service_secret`). Properties:

- tied to one run, with a 300-second lifetime (long enough to open the stream);
- verified by the service on its own, no round trip needed;
- implemented with only the standard library (`service/auth.py`).

It can be used more than once within its lifetime (it is not single-use).

### 5.2 Service → Frappe: secret plus acting user

Every call from the service carries the shared secret in a dedicated header, `X-Frappe-AI-Service-Secret` (not `Authorization`, which Frappe core intercepts), and the site name. Calls that act for a user also carry the **user** and the **run**. Frappe then:

1. checks the secret;
2. for tool calls, checks the run exists, is *Running* or *Paused*, and **belongs to that user**;
3. for risky tools, checks the user's recorded approval;
4. counts the call against the run's budgets;
5. switches to that user (`frappe.set_user`) and runs the tool, so all normal permission rules apply.

The secret proves the caller is the service process; it cannot by itself be used to act as an arbitrary user, because the call must name a live run that user owns. A leaked secret is still serious: an attacker could act as any user *who currently has an active run*, within that run's limits. Treat the secret like a password and keep port 8001 private.

### 5.3 Ownership checks

Writes made by Frappe callbacks use `ignore_permissions=True`, so these checks are the only protection between a user and someone else's data:

- `assert_session_owner` and `assert_run_owner`: owner, or `write` permission.
- `get_run_config` and `dispatch_*`: the run's owner must equal the acting user.
- `AI Run` is **final once Completed or Failed**: later `persist_run_result` or `fail_run` calls are ignored, so a late callback cannot overwrite a stopped or finished run.

### 5.4 Budgets

Permissions limit what an agent can touch; budgets limit how much. Per run: tool calls, mutating calls, records per call, and active time ([ADR 0008](../decisions/0008-execution-budgets.md)). They are enforced when Frappe dispatches a tool.

## 6. Data

MariaDB (Frappe) holds everything that matters. LanceDB is a rebuildable cache.

| Store | Holds |
|---|---|
| MariaDB | All DocTypes; chunk text and metadata |
| LanceDB (`sites/<site>/private/files/lancedb`) | Embedding vectors and keyword indexes |

Three LanceDB tables:

| Table | Content | Has vectors | Removed when |
|---|---|---|---|
| `chunks` | Knowledge chunks. Row `id` = the `AI Knowledge Chunk` name | yes | its source or knowledge base is deleted |
| `chat_attachment_chunks` | Large attachments in a session | yes | the session is deleted or logs are cleared |
| `memories` | Agent memory (keyword search only) | no | the memory is deleted |

`AI Knowledge Chunk` uses auto-increment names because the integer name *is* the LanceDB row id. Changing that naming breaks retrieval. See [ADR 0002](../decisions/0002-lancedb-vector-store.md).

## 7. Streaming format

Server-Sent Events (`text/event-stream`, headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`). Each frame is `event: <name>` and `data: <json>`; the JSON also carries `type`.

| Event | Payload | Meaning |
|---|---|---|
| `run_started` | `run`, `session` | Stream opened |
| `text` | `content` | A piece of the answer |
| `tool_started` | `id`, `name`, `arguments` | The agent began a tool call |
| `tool_ended` | `id`, `name`, `result` | The tool call returned |
| `error` | `message`, `code`, `status_code`, `retryable`, optional `diagnostics` | The run failed |
| `done` | `status`, `iterations`, `output`, `usage`, optional `questions` | Run ended (Completed) or paused (Paused) |

Details for client authors are in [005 Frontend contract](005-frontend-contract.md).

## 8. What happens when something fails

| Failure | What happens |
|---|---|
| Service is down or unreachable | `start_run` still succeeds; the browser's stream request fails. The orphaned *Running* run is failed on the next session load (`recover_session`) or, if still *Running* after 300 s, when the user sends the next turn (`AISession.assert_not_blocked`) |
| Browser closes mid-stream | The service marks the run Failed ("Stream interrupted") |
| A tool raises an error | The error text (cut to 500 characters) goes back to the model as `{"error": …}`; the run continues |
| The model call fails | One retry on the system default model, only if no tool had completed yet in that turn (retrying after a tool ran could repeat its side effects). Otherwise the run fails with a normalized error |
| The user clicks Stop | The run is marked Failed ("Stopped by user"); a late result from the service is ignored |
| Limits exceeded (calls, changes, records, time) | The tool call is refused with a budget error |
| Embedding service (Ollama) is down | Chat works; large attachments fall back to being inlined; knowledge ingestion marks the source Failed and knowledge search reports the error |
| Index metadata or vector size changed | Searches are refused until you rebuild (`rebuild_knowledge_index`) |

A restart of the service does not resume a run in the middle: the run fails and the user retries ([ADR 0007](../decisions/0007-failure-over-durable-execution.md)).

## 9. Deployment

Two programs run side by side. Under `bench start`:

```
web: bench serve                                    # Frappe, port 8000
ai:  uvicorn frappe_ai.service.main:app --port 8001 # the service
```

The `ai` line goes in the bench `Procfile`; see [setup](../setup.md). The service uses the bench's Python environment but never starts Frappe itself. Embeddings need a private Ollama server with the fixed `nomic-embed-text` model ([ADR 0016](../decisions/0016-fixed-ollama-embeddings.md)).

Chat calls go through one OpenAI-compatible client for every provider ([ADR 0014](../decisions/0014-openai-compatible-chat-transport.md)). The `litellm` package is used only to suggest provider and model names in the UI ([ADR 0013](../decisions/0013-litellm-for-provider-ux-agno-still-executes.md)).

Assumptions:

1. The service and Frappe are on the same host or a trusted network. The secret does not replace network isolation.
2. The browser can reach port 8001, directly or through a reverse-proxy route.
3. One service instance serves one site (it reads one site's secret at startup).
4. LanceDB is embedded and written only by Frappe background workers.

## 10. How to check it works

1. Both programs start under `bench start`, and `GET :8001/health` returns `{"status": "ok", "frappe_reachable": true}`.
2. A chat streams text without using a Frappe worker for the model call.
3. A user without permission on a DocType asks the agent to read it: the tool refuses. This is the decisive test of the invariant in §3.
4. Every run leaves an `AI Run` with its settings snapshot, tool calls, usage and iteration count.
5. Deleting a knowledge source removes its MariaDB chunks and its LanceDB rows.

## Where to go next

- [003 DocType reference](003-doctype-reference.md): every record type
- [005 Frontend contract](005-frontend-contract.md): the API the UI uses
- [ADR index](../README.md#decisions-why-it-is-this-way)
