# ADR 0001 — Run agents in a separate FastAPI service (Agno), not in Frappe

**Status:** Accepted · **Date:** 2026-08-05

## The problem

The older app `flow` ran agents inside Frappe's web workers. A run could last tens of seconds, because the model is slow and the agent may loop through several tool calls, and the whole time it held one worker. With a default setup that means roughly 10–20 runs at once, and every other Desk user slows down while agents are busy. Adding workers scales memory for the whole Frappe app, not just for AI. `flow` also had about 1,000 lines of hand-written agent loop (tool schemas, streaming, pause and resume, usage counting).

## The decision

Move the agent loop out of Frappe into a **separate FastAPI service built on the Agno agent library**. Frappe keeps everything else: configuration, storage, permissions, and trigger detection.

- Frappe is the single source of truth.
- The service (`uvicorn`, port 8001) is **stateless**: it fetches the run's configuration from Frappe each time and keeps no credentials at rest.
- Agno provides the run loop, tool schemas and streaming that `flow` hand-wrote.

## What follows

**Good**
- Waiting on the model no longer uses Frappe workers. The limit becomes the model provider's rate limit.
- A slow model does not slow the Desk.
- The service can be scaled separately.
- Less custom code, and Agno brings multi-agent teams, reasoning modes and MCP support.

**Costs**
- Two programs to run (`bench start` must supervise `uvicorn`; production needs a second service).
- Every tool that touches Frappe is an HTTP round trip back to Frappe.
- A new trust boundary between the service and Frappe, handled by [ADR 0003](0003-tools-execute-in-frappe.md).
- New dependencies: `agno`, `fastapi`, `uvicorn`.
- Debugging spans two processes, so a run id is needed in logs from both.

DocType-driven configuration is unchanged: `flow`'s `assemble()` becomes `AgentBuilder.build()`, still editable in the Desk with no restart.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Keep `flow` as it is | Does not remove the worker limit, which is the whole reason for the project |
| Keep the Frappe-only design, but run agents as background jobs | Streaming would need extra hops through Redis and socket.io, background workers are also a fixed pool (the limit only moves), and the custom loop stays |
| FastAPI without Agno (calling the model client directly) | Solves concurrency but keeps the hand-written loop |
| Agno inside Frappe's process | Agno is async; Frappe's web stack is not. Bridging per request would block workers again |

## How we know it works

25 concurrent chat streams leave the Desk responsive.

## Related

[001 Architecture](../specifications/001-architecture.md), [ADR 0003](0003-tools-execute-in-frappe.md), [ADR 0004](0004-sse-direct-from-fastapi.md).
