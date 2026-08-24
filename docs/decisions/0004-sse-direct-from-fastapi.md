# ADR 0004 — Stream SSE directly from FastAPI to the browser

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Context

`flow` streams agent output to the browser as **Server-Sent Events** generated inside the
Frappe request/response cycle. Notably, `frappe.publish_realtime` is never used anywhere in
`flow` — there is no socket.io involvement at all.

That design forces `flow` into awkward transaction handling: WSGI iterates a streamed
response body *after* the request handler returns, by which point Frappe's end-of-request
commit has already fired. `stream_with_persistence` therefore commits explicitly on `Done`
and on exception, and its `finally` block marks the run failed if `GeneratorExit` (client
disconnect) cuts the stream short.

More importantly, it holds a gunicorn worker for the entire run.

With orchestration moving to FastAPI ([ADR 0001](0001-agno-fastapi-over-frappe-native.md)),
the streaming path must be re-decided.

---

## Decision

**The browser opens the SSE connection directly against FastAPI (`:8001`)**, authenticated
with a short-lived, run-scoped token minted by Frappe.

Flow of control:

1. Browser calls `frappe_ai.api.start_run` on Frappe (`:8000`).
2. Frappe creates the `AI Session`/`AI Run`, persists the user message, and mints a token.
3. Frappe returns `{run, session, token, stream_url}`.
4. Browser opens a `fetch`-based POST against `stream_url` with `Bearer <token>`; this
   preserves SSE framing while allowing the request body used by resume.
5. FastAPI verifies the token, builds the agent, and streams events.
6. On completion, FastAPI posts the result back to Frappe for persistence.

### Token properties

- HMAC over `(run, session, user, expiry)` signed with the shared
  `frappe_ai_service_secret` in `site_config.json` (see ADR 0011).
- **Bound to a single run** — cannot be replayed against another run or user.
- Short TTL (default 300s), covering stream setup only, not stream duration.
- Verified locally by FastAPI, then confirmed against Frappe (run still `Running`).

### Wire format

Deliberately compatible with `flow`'s event shapes, while the current React client parses
the stream through its fetch-based transport adapter:

| Event | Payload |
|---|---|
| `run_started` | `{run, session}` |
| `text` | `{content}` |
| `tool_started` | `{name, arguments}` |
| `tool_ended` | `{name, result}` |
| `error` | `{message}` |
| `done` | `{status, iterations, output, usage, questions?}` |
| `ping` | `{}` — keep-alive, see below |

Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

### Heartbeats (required)

`flow` never needed keep-alives: it streamed through Frappe's own connection, which the
desk's infrastructure already kept open. Streaming from a **separate port** changes that —
any reverse proxy, load balancer, or ingress between the browser and `:8001` will terminate
a connection that goes idle longer than its timeout (commonly 30–60s). A long reasoning step
or a slow tool call exceeds that easily.

The service therefore emits `event: ping` every 15 seconds whenever no other event has been
sent. The client treats it purely as liveness and renders nothing.

Without this, streaming works on localhost and fails in most real deployments — a failure
mode that does not appear in development. Scheduled for Phase 8.1; the interval is
configurable via `AI Settings.heartbeat_interval`.

---

## Consequences

### Positive

- **Frappe workers are never held open during a run.** This is the entire point of the
  migration; proxying would have forfeited it.
- **Lowest latency.** Tokens go straight from the service to the browser with no
  intermediate hop.
- **`flow`'s commit choreography disappears.** No WSGI post-response iteration, so no
  explicit commit-on-`Done`, no `GeneratorExit` special-casing. An entire class of bug is
  designed out rather than ported.
- **Frontend port is mechanical.** Only `api/stream.js` changes — new origin, `Bearer`
  token instead of `X-Frappe-CSRF-Token`. The SSE parsing (split on `\n\n`) is unchanged.
- **Backpressure and cancellation are native.** FastAPI/Starlette surface client
  disconnects directly, so cancelling an abandoned run is straightforward.

### Negative

- **Cross-origin.** The browser talks to two ports, so CORS must be configured on the
  service (allowing the site origin only, with credentials). In production a reverse proxy
  should map `/ai-stream` to `:8001` to keep a single public origin.
- **New auth surface.** Token minting and verification is security-critical code that did
  not exist in `flow` (which relied on the desk session cookie).
- **`:8001` must be browser-reachable**, directly or via proxy — an additional deployment
  requirement, and a hardening obligation: the port must not be publicly exposed without
  the proxy.
- **Split logs.** A failed stream may need correlating across both processes.

### Neutral

- Stale-run recovery is still needed on the Frappe side (`RUNNING_STALE_SECONDS = 300`,
  `recover_session`, `stop_run`), now complemented by service-side task cancellation.

---

## Alternatives Considered

### Proxy the stream through Frappe
Browser talks only to `:8000`; Frappe relays the FastAPI stream. Single origin, no CORS, no
token plumbing — reuses the desk session cookie.
**Rejected:** it occupies a Frappe worker for the full duration of every run, which is
precisely the constraint this migration exists to remove. It would deliver the operational
cost of two processes with none of the concurrency benefit.

### Frappe realtime (socket.io)
FastAPI publishes events to Redis; Frappe's socket.io pushes them to the browser. Reuses
desk auth, survives reconnects, and matches how other Frappe apps do live updates.
**Rejected:** adds a hop and a Redis dependency on the hot path, and is a larger deviation
from `flow`'s model — meaning a bigger frontend rewrite. Worth revisiting if reconnection
robustness becomes a real requirement, since SSE reconnect semantics are weaker.

### Polling `AI Run`
Browser polls for incremental output. Simple and requires no new auth.
**Rejected:** token-by-token streaming is the expected UX; polling at a useful granularity
would generate more Frappe load than the design it replaces.

---

### Replayable streams (`Last-Event-ID`) — deferred

SSE has a standard resume mechanism: the server tags events with `id:`, and a reconnecting
client sends `Last-Event-ID` to resume from that point. This would let a user recover a
stream after a network blip without losing output.

**Deferred to Phase 8.3, conditional on need.** It requires persisting every event per run
in an ordered, addressable store — new infrastructure whose value depends entirely on how
often disconnects actually happen. Heartbeats (above) eliminate the *predictable* cause of
disconnection, which is idle-timeout termination. What remains is genuine network loss,
where the existing behaviour — the run completes server-side and its full output is readable
from `AI Run.output` — is already an acceptable fallback.

Build this only if heartbeats prove insufficient in practice.

---

## Verification

- A chat turn streams tokens visibly incrementally, not as one block at the end.
- During a long run, the Frappe desk stays responsive and worker count is unaffected.
- A token for run A cannot stream run B.
- An expired token is rejected.
- Closing the browser tab mid-run cancels the service task and marks the run failed.
- 25 concurrent streams do not degrade the desk.

---

## References

- [001 — Architecture §5, §6, §8](../specifications/001-architecture.md)
- [ADR 0001 — Agno + FastAPI](0001-agno-fastapi-over-frappe-native.md)
- `apps/flow/flow/api/api.py` — `_sse_response`, `_format_sse`
- `apps/flow/frontend/src/api/stream.js` — the client being adapted
