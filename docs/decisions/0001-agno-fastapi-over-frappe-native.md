# ADR 0001 — Agno + FastAPI instead of a Frappe-native runtime

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Context

`apps/flow` implements a complete AI agent framework natively inside Frappe: a hand-rolled
orchestration loop over litellm, DocType-driven configuration, a LanceDB RAG pipeline,
triggers, memory, and an SSE-streaming chat panel. It works.

Its limitation is structural rather than incidental. Every agent run executes synchronously
inside a Frappe gunicorn worker and holds that worker for the entire duration of the LLM
call — including every tool-calling iteration, of which there may be up to `max_iterations`
(default 10, 40 for the built-in assistant). A single conversation can occupy a worker for
tens of seconds.

Consequences at even modest load:

- Practical concurrency ceiling of roughly 10–20 simultaneous runs.
- Agent traffic degrades desk responsiveness for *all* users on the site, including those
  not using AI features at all.
- Scaling requires adding gunicorn workers, which scales memory for the whole Frappe app,
  not just the AI workload.
- No path to scaling the AI workload independently of the ERP workload.

Additionally, `flow`'s orchestration loop is bespoke: tool schema derivation, streaming
tool-call assembly, pause/resume, and usage accounting are all hand-maintained code that
duplicates what mature agent frameworks provide.

---

## Decision

Move agent orchestration out of the Frappe process into a **separate FastAPI service built
on the Agno agent framework**, while keeping configuration, persistence, authorization, and
trigger detection in Frappe.

Concretely:

- Frappe remains the application layer and the single source of truth.
- FastAPI (`uvicorn`, port 8001) hosts the async orchestration layer.
- Agno replaces `flow/lib/agent.py`, `flow/lib/tool.py`, and the streaming machinery.
- The service is **stateless** — it fetches config per run and holds no credentials at rest.

---

## Consequences

### Positive

- **Concurrency.** Async orchestration means the ceiling becomes the LLM provider's rate
  limit rather than the worker pool. Target: 100+ concurrent runs.
- **Isolation.** LLM latency no longer affects desk responsiveness. This is the single
  biggest user-visible improvement.
- **Independent scaling.** The AI service scales horizontally without touching Frappe.
- **Less bespoke code.** Agno owns the run loop, tool schemas, streaming, and multi-agent
  orchestration. `flow` maintained roughly 1,000 lines of this by hand.
- **Framework capabilities for free.** Multi-agent teams, reasoning modes, and native MCP
  support — none of which `flow` has.

### Negative

- **Two processes.** `bench start` must supervise uvicorn; production needs a second
  service unit. Real operational cost.
- **Network hop for tools.** Every Frappe-touching tool call is HTTP round-trip
  FastAPI→Frappe. Adds latency versus in-process calls.
- **A new security boundary.** The service↔Frappe channel must be authenticated and must
  preserve per-user identity — the risk addressed by
  [ADR 0003](0003-tools-execute-in-frappe.md).
- **New dependencies.** `agno`, `fastapi`, `uvicorn` are not currently installed.
- **Debugging spans two processes.** Correlation IDs on every run become necessary rather
  than optional.

### Neutral

- DocType-driven configuration is unchanged. `assemble()` becomes `AgentBuilder.build()` —
  same pattern, different runtime class. Config remains UI-editable and restart-free.

---

## Alternatives Considered

### Keep `flow` as-is
Rejected. Does not address the concurrency ceiling, which is the reason for the project.

### Keep the Frappe-native design but run agents in background jobs
Runs would move to RQ workers, freeing web workers. But streaming would then require
socket.io round-trips through Redis, background workers are also a fixed pool (so the
ceiling moves rather than disappears), and none of the bespoke orchestration code goes
away. Rejected as a partial fix with comparable complexity.

### FastAPI without Agno (direct litellm)
Solves concurrency, but keeps ~1,000 lines of hand-rolled orchestration. Rejected — if the
runtime is being replaced anyway, adopting a maintained framework is nearly free.

### Agno embedded in-process (no FastAPI)
Agno is async-native; Frappe's WSGI stack is not. Bridging with `asyncio.run` per request
would reintroduce worker blocking, forfeiting the entire benefit. Rejected.

---

## Verification

The decision is validated when 25 concurrent chat streams leave the Frappe desk responsive
— the failure mode `flow` exhibits and this architecture exists to eliminate.

---

## References

- [001 — Architecture](../specifications/001-architecture.md)
- [ADR 0003 — Tools execute in Frappe](0003-tools-execute-in-frappe.md)
- [ADR 0004 — SSE direct from FastAPI](0004-sse-direct-from-fastapi.md)
