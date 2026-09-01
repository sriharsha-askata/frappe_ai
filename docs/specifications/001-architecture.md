# 001 — Architecture Specification

**Status:** Approved. Core runtime/parity phases 1–5 are implemented; the Phase 6
frontend runtime is implemented in React/esbuild, but the full-page host layout is still
being separated from the slide-in panel shell. Assistant Core/FAC migration is active,
and Phase 8 production-hardening items remain outstanding.
**Applies to:** `apps/frappe_ai`
**Supersedes:** nothing (new app)

---

## 1. Purpose

`frappe_ai` provides AI agent capabilities inside Frappe: conversational agents with
tools, retrieval-augmented knowledge, event-driven automation, persistent memory, and a
full audit trail.

It is a reimplementation of the `flow` app's feature set on a different runtime.
`flow` runs its LLM orchestration synchronously inside the Frappe web worker; `frappe_ai`
moves orchestration into a separate asynchronous FastAPI service built on the Agno agent
framework, while keeping all configuration, persistence, and **authorization** in Frappe.

`flow` is the functional specification for this app and will be uninstalled once
`frappe_ai` reaches parity. No data migration between the two is in scope.

---

## 2. Problem Statement

The `flow` architecture has one structural limit: every agent run occupies a Frappe
gunicorn worker for the entire duration of the LLM call, including all tool-calling
iterations. With a default worker pool, this caps practical concurrency at roughly 10–20
simultaneous agent runs, and a busy agent degrades desk responsiveness for every other
user on the site.

Everything else about `flow` — the DocType-driven configuration, the audit trail, the
trigger system, the permission-gated sandbox — is sound and worth preserving.

The objective is therefore narrow: **change the execution substrate, preserve the model.**

---

## 3. Component Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                            Browser                                     │
│        Frappe Desk / site page  +  frappe_ai React frontend           │
└───────────┬───────────────────────────────────────┬───────────────────┘
            │                                       │
            │ (1) config CRUD,                      │ (2) SSE token stream
            │     session history,                  │     Bearer <run token>
            │     run control                       │
            ▼                                       ▼
┌───────────────────────────────────┐   ┌───────────────────────────────┐
│      Frappe  :8000                │   │      FastAPI  :8001           │
│                                   │   │                               │
│  AI * DocTypes (source of truth)  │   │  AgentBuilder                 │
│  ├─ Provider / Model / Settings   │◄──┤   DocType config → Agno Agent │
│  ├─ Agent / Tool / MCP Connection │(3)│                               │
│  ├─ Session / Message / Run       │   │  Agno run loop (async)        │
│  ├─ Knowledge Base/Source/Chunk   │   │   ├─ LLM calls                │
│  └─ Trigger / Agent Memory        │   │   ├─ tool calling             │
│                                   │   │   └─ streaming                │
│  Whitelisted API                  │   │                               │
│  ├─ chat / resume / stop          │   │  LanceDB (vectors)             │
│  ├─ tool dispatch  ◄──────────────┼───┤   knowledge + attachments     │
│  └─ persistence callbacks ◄───────┼───┤                          (4)  │
│                                   │(5)│                               │
│  Triggers (doc_events + cron) ────┼──►│                               │
│  safe_exec sandbox                │   │  Stateless. No credentials    │
│  LanceDB (derived indexes)        │   │  at rest. No DB access.       │
│  Permission enforcement           │   │  No direct LanceDB access.    │
└───────────────────────────────────┘   └───────────────────────────────┘
```

**Numbered flows:**

1. **Config & history** — the frontend reads/writes state through standard Frappe
   endpoints and a frontend-oriented JSON/BFF layer. Unchanged authorization model.
2. **Streaming** — the browser opens an SSE connection directly to FastAPI, authenticated
   with a short-lived token minted by Frappe. Frappe workers are not held open.
3. **Config fetch** — FastAPI pulls agent configuration from Frappe at the start of each
   run (stateless; no cache that could go stale after a DocType edit).
4. **Retrieval** — Frappe-side knowledge and memory modules access the site-scoped
   LanceDB store from the tool/persistence boundary. The FastAPI service remains
   stateless and does not open a Frappe database connection.
5. **Tool dispatch & persistence** — every Frappe-touching tool call, and every message/run
   write, goes back to Frappe over HTTP carrying the originating user's identity.

---

## 4. Responsibility Boundaries

This table is the load-bearing rule of the design. When in doubt during implementation,
resolve the question against it.

| Concern | Owner | Rationale |
|---|---|---|
| Configuration (agents, models, tools, KBs) | **Frappe** | UI-editable, versioned, restart-free |
| Credentials (API keys) | **Frappe** | Password fields; never persisted in the service |
| Audit rows (`AI Run`, messages) | **Frappe** | Single audit database; compliance |
| **Authorization for every data operation** | **Frappe** | `frappe.has_permission` is the only permission authority |
| Tool *execution* against Frappe data | **Frappe** | Follows authorization; cannot be separated from it |
| Sandboxed code execution (Script tools, conditions) | **Frappe** | `safe_exec` namespace lives with the data it guards |
| LLM orchestration / run loop | **FastAPI + Agno** | The async work that motivated the split |
| Streaming to the browser | **FastAPI** | Long-lived connections must not occupy Frappe workers |
| Vector storage and similarity search | **LanceDB**, accessed by Frappe knowledge/memory modules | Site-local derived store; writes and retrieval stay behind the Frappe boundary |
| Trigger *detection* (doc events, cron) | **Frappe** | Requires in-process hooks and the scheduler |
| Trigger *execution* | **FastAPI** | Same run path as interactive chat |

### The invariant

> **FastAPI never reads or writes the Frappe database directly, and never holds a
> credential at rest. It orchestrates; Frappe authorizes.**

Violating this collapses per-user permissions into a single service identity, which is the
principal risk of the two-process design. See
[ADR 0003](../decisions/0003-tools-execute-in-frappe.md).

---

## 5. Request Lifecycles

### 5.1 Interactive chat turn

```
Browser                Frappe :8000                      FastAPI :8001
   │
   ├─ POST api/method/frappe_ai.api.start_run
   │        ──────────────►
   │                       resolve/create AI Session
   │                         (switches session.model if `model` passed
   │                          and no run is in flight)
   │                       create AI Run (status=Running,
   │                                      config_snapshot)
   │                       persist user message
   │                       mint short-lived run token
   │        ◄──────────────  {run, session, token, stream_url}
   │
   ├─ POST stream_url, Bearer token ─────────────────────►
   │                                              verify token w/ Frappe
   │                                              GET agent config ──►
   │                       ◄──────────────────────
   │                                              build Agno Agent
   │                                              ┌── LLM call (async)
   │   ◄── event: text ─────────────────────────  │   token deltas
   │                                              │
   │   ◄── event: tool_started ─────────────────  ├── tool call
   │                                              │
   │                       ◄─ POST tool dispatch ─┤   (as originating user)
   │                          has_permission?     │
   │                          execute builtin     │
   │                       ─► result ─────────────┤
   │   ◄── event: tool_ended ────────────────────  │
   │                                              └── loop until no tool calls
   │                                                  or max_iterations
   │                       ◄─ POST persist_result ─
   │                          append messages,
   │                          apply_result to AI Run
   │   ◄── event: done ──────────────────────────
```

### 5.2 Confirmation pause / resume

Tools with `requires_confirmation = 1` do not execute on first encounter. The service
emits a `done` event with `status: Paused` and a `questions` payload; Frappe stores the
pending questions on the `AI Run`. The panel renders a confirmation card. On answer, the
browser calls `resume_run`, which mints a fresh token and restarts the stream with the
answers injected as tool results.

Answer semantics (preserved verbatim from `flow`):

| Answer | Behaviour |
|---|---|
| `Approve` | Execute the tool, continue the loop |
| `Deny` | Return `{"status": "denied"}` **and halt the entire run** |
| free text | Return `{"status": "redirect", "user_feedback": …}`; the model adapts and retries |

### 5.3 Trigger run

```
Document event in Frappe
   │
   ├─ hooks.py doc_events "*" → frappe_ai.triggers.dispatch
   │     filter enabled AI Triggers matching (doctype, event)
   │     evaluate condition AS the trigger's run_as identity   ← fail-closed
   │     frappe.enqueue(fire, enqueue_after_commit=True)
   │
   └─ background worker: frappe_ai.triggers.fire
         frappe.set_user(run_as)                               ← identity for the whole run
         re-load doc (None if deleted)
         RE-EVALUATE condition                                 ← guards state drift
         render Jinja prompt_template with {doc, now}
         POST to FastAPI /run  (non-streaming, auto_approve per trigger)
         persist AI Run with source=Trigger, reference_doctype/name
```

Scheduled triggers follow the same `fire` path, driven by a `*/5 * * * *` cron job that
anchors `croniter` on `last_fired_at or creation`.

---

## 6. Authentication & Authorization

### 6.1 Browser → FastAPI (run tokens)

Frappe mints a short-lived, single-run token when a run starts. It is an HMAC over
`(run, session, user, expiry)` signed with the shared secret in `site_config.json`'s
`frappe_ai_service_secret` (not a DocType field — see
[ADR 0011](../decisions/0011-service-secret-in-site-config.md)).

- Bound to one `AI Run`; cannot be replayed against another.
- Short TTL (default 300s), covering stream setup only.
- FastAPI verifies the signature locally, then confirms the run is still `Running` with
  Frappe before streaming.

### 6.2 FastAPI → Frappe (service identity + acting user)

The service authenticates to Frappe with the shared secret and passes the **originating
user** on every call. Frappe's dispatch endpoint executes the requested tool under that
user via `frappe.set_user`, so:

- `frappe.has_permission` applies exactly as it does in the desk.
- User permissions, role permissions, and `if_owner` rules apply.
- `safe_exec` restrictions apply to Script tools and conditions.

**A compromised or buggy service cannot exceed the permissions of the user on whose
behalf it is acting** — the property that would be lost under the "service user calls REST
API" pattern.

### 6.3 Ownership chokepoints

Ported from `flow` unchanged, because persistence writes use `ignore_permissions=True`
and these are the only things standing between a user and someone else's conversation:

- `_assert_session_owner` — owner match **or** `write` permission, else `PermissionError`
- `assert_run_owner` — same, for runs
- `AgentBuilder` — checks agent `enabled`, `read` permission on `AI Model`, model `enabled`

---

## 7. Data Architecture

### 7.1 Source of truth

**MariaDB is authoritative for everything.** LanceDB is a derived, disposable index that
can be rebuilt from `AI Knowledge Chunk` rows at any time.

| Store | Contents | Rebuildable |
|---|---|---|
| MariaDB (Frappe) | All DocTypes, chunk text, chunk metadata | — |
| LanceDB | Embedding vectors + BM25 FTS indexes | **Yes**, from MariaDB |

`AI Knowledge Chunk` uses `autoincrement` naming, and its integer name is the LanceDB
row `id`. This join key is load-bearing — changing the naming rule silently breaks
retrieval hydration.

### 7.2 LanceDB tables

Three tables share one site-scoped DB path (`private/files/lancedb`, or `lancedb_test`
under `frappe.flags.in_test`):

| Table | Contents | Vectors? | Lifetime |
|---|---|---|---|
| `chunks` | Curated knowledge, `id` = `AI Knowledge Chunk` name | yes | Until source/KB deleted |
| `chat_attachment_chunks` | Oversized session attachments | yes | Until session deleted / log clearing |
| `memories` | Agent memory, BM25 index | **no** | Until memory deleted |

Agent memory uses a **vectorless** LanceDB table indexed for BM25 full-text search, kept in
sync by `AI Agent Memory`'s `on_update`/`on_trash`. Index failures are logged and never
block a memory write; search failures degrade to recency.
See [ADR 0002](../decisions/0002-lancedb-vector-store.md).

---

## 8. Streaming Protocol

SSE over `text/event-stream`. The event shapes remain compatible with `flow`; the current
React client parses the stream through a fetch-based transport adapter because resume
requests carry a JSON body.

| Event | Payload |
|---|---|
| `run_started` | `{run, session}` |
| `text` | `{content}` — token delta |
| `tool_started` | `{name, arguments}` |
| `tool_ended` | `{name, result}` |
| `error` | `{message}` |
| `done` | `{status, iterations, output, usage, questions?}` |

Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

`flow`'s elaborate commit-on-`Done` / `GeneratorExit` handling is **not** ported. It
existed because WSGI iterates a streamed response body after the request handler returns,
so Frappe's end-of-request commit had already fired. FastAPI streams natively and
persistence is an explicit call back to Frappe, so this class of bug does not arise.

---

## 9. Failure Handling

| Failure | Handling |
|---|---|
| FastAPI unreachable | `start_run` fails fast with a clear message; no orphaned `AI Run` |
| Client disconnects mid-stream | Service cancels the run; marks the `AI Run` failed via callback |
| Run exceeds `max_iterations` | Run marked `Failed` with an explicit error |
| Tool raises | Caught, returned to the model as `{"error": …}` truncated to 500 chars — a failing tool never kills the run |
| Stale `Running` run | Auto-failed after `RUNNING_STALE_SECONDS` (300); also `recover_session` on reload and explicit `stop_run` |
| LanceDB unavailable | `search_knowledge` fails closed with an error; the rest of the run continues |
| Ollama embedding service unavailable | Normal chat continues; attachment retrieval falls back to inline mode; knowledge ingestion/search reports the service error |
| Ollama model digest or vector dimension changed | Fixed-model LanceDB metadata/dimension checks reject mixed vectors; rebuild from MariaDB |

---

## 10. Deployment

Two processes under `bench start`:

```
web:     bench serve                      # Frappe, :8000
ai:      uvicorn frappe_ai.service.main:app --port 8001
```

The service runs in the bench Python environment but does **not** call `frappe.init` /
`frappe.connect` — all Frappe access is over HTTP. This keeps it independently
deployable and horizontally scalable; the concurrency ceiling becomes the LLM provider's
rate limit rather than the gunicorn worker count.

New dependencies: `agno`, `fastapi`, `uvicorn`. Embeddings require a private Ollama
service serving the fixed `nomic-embed-text` model; its endpoint is configured through
`FRAPPE_AI_OLLAMA_BASE_URL` (see [setup](../setup.md)).
Already present in this bench: `lancedb 0.36.0`, `openai 2.30.0`, `pydantic 2.11.7`.

> `litellm` is a declared dependency for provider validation and model-id suggestions
> only. Chat calls and embedding calls do not go through litellm: chat uses the shared
> OpenAI-compatible transport, and embeddings use the fixed Ollama OpenAI-compatible
> endpoint. See [ADR 0014](../decisions/0014-openai-compatible-chat-transport.md) and
> [ADR 0016](../decisions/0016-fixed-ollama-embeddings.md).

---

## 11. Assumptions

1. FastAPI and Frappe run on the same host or a trusted network. The shared secret is not
   a substitute for network isolation; `:8001` should not be publicly exposed.
2. The browser can reach `:8001` directly (same host, or a reverse-proxy route).
3. Site count is small enough that one service instance can serve all sites; the site name
   is carried per request.
4. LanceDB runs embedded against the site's private files directory from Frappe-side
   knowledge and memory code. Ingestion writes stay in Frappe background workers
   (single-writer discipline); the FastAPI service does not need direct filesystem access
   to the LanceDB store.

---

## 12. Acceptance Criteria

The architecture is correctly implemented when:

1. Both processes start under `bench start`; `GET :8001/health` returns 200.
2. An agent run streams tokens to the browser without occupying a Frappe worker for the
   duration of the LLM call.
3. **A non-System-Manager user asking the agent to read a DocType they lack permission on
   is refused by the tool.** This is the decisive test for §4's invariant.
4. Every run produces an `AI Run` with config snapshot, tool calls, usage, and iterations.
5. Deleting a knowledge source purges both MariaDB rows and LanceDB entries.
6. 25 concurrent chat streams leave the desk responsive.

---

## References

- [002 — Feature Mapping](002-feature-mapping.md)
- [003 — DocType Reference](003-doctype-reference.md)
- [ADR 0001 — Agno + FastAPI over Frappe-native](../decisions/0001-agno-fastapi-over-frappe-native.md)
- [ADR 0003 — Tools execute in Frappe](../decisions/0003-tools-execute-in-frappe.md)
- Reference implementation: `apps/flow` (to be uninstalled after parity)
