# ADR 0007 — Fail-and-retry instead of durable mid-run execution

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru
**Prompted by:** production-readiness review, which listed "no durable execution model" as
its top P0 concern

---

## Context

Moving orchestration into a separate FastAPI process
([ADR 0001](0001-agno-fastapi-over-frappe-native.md)) raises a question `flow` never had to
answer: what happens to an in-flight agent run when the service restarts?

In `flow`, a run lived inside a Frappe web worker. A restart killed it and the run was
simply lost — but the blast radius was one worker among many, and the failure was visible
to the user immediately because their HTTP request died with it.

With a separate service, a restart (deploy, crash, OOM, autoscale event) can terminate many
concurrent runs at once, and the browser's SSE connection dies without the Frappe side
necessarily knowing why.

A production-readiness review flagged this as critical, recommending a durable execution
model — checkpointing run state after each iteration so a restarted process can resume
mid-conversation.

---

## Decision

**Runs fail cleanly on process loss. They are not resumed mid-flight.**

Three existing mechanisms, all ported from `flow`, make failure clean rather than silent:

| Mechanism | Behaviour |
|---|---|
| `RUNNING_STALE_SECONDS = 300` | A run still `Running` after 300s with no progress is auto-failed |
| `recover_session` | On session reload, any orphaned `Running` runs are marked `Failed` |
| `stop_run` | The user can terminate a run explicitly |

The user's recovery path is to retry, which starts a fresh run against the same session.
Conversation history is preserved (messages persist to Frappe as they are produced), so a
retry does not lose context — only the incomplete turn.

### Where durability *is* provided

**Trigger runs are durable**, because they are the case with no human watching. They are
dispatched with `frappe.enqueue(..., enqueue_after_commit=True)`, so RQ provides
at-least-once delivery and retry. A worker dying mid-trigger results in redelivery, not a
lost automation.

This is the deliberate split: **durability where nobody is watching, fast failure where
someone is.**

---

## Consequences

### Positive

- **No checkpointing machinery.** Persisting and restoring mid-run state is not merely
  storage — a resumed run must not re-execute tool calls whose side effects already
  landed. Getting that wrong means duplicate records, duplicate submissions, duplicate
  emails. Avoiding the problem entirely is worth a great deal.
- **Retry is cheap and obvious.** For interactive chat, re-asking is a few seconds and one
  LLM call. Resume machinery would cost far more than it saves.
- **Failures are visible, never silent.** The three mechanisms above guarantee no run sits
  in `Running` forever. A user always learns their request failed.
- **The unattended path is already durable.** The case that genuinely needs delivery
  guarantees has them, via infrastructure Frappe already runs.
- **Statelessness is preserved.** The service holds no run state between requests, which is
  what makes horizontal scaling a matter of adding instances.

### Negative

- **Long runs lose work on restart.** A 40-iteration assistant run killed at iteration 38
  restarts from zero — wasted tokens and wasted time.
- **Deploys interrupt users.** Rolling the service mid-conversation fails those runs.
  Mitigation is operational: drain connections before restart, deploy off-peak.
- **Token cost of retries.** A retried run re-pays for the whole conversation prefix.
  Cost accounting (Phase 8.3) will make this visible.
- **Interactive and trigger paths differ.** Two behaviours to explain and to test.

### Neutral

- The 300-second staleness window is a tunable, not a law. If runs legitimately exceed it,
  raise it — but note it is also the ceiling on how long a dead run stays `Running`.

---

## Alternatives Considered

### Mid-run checkpoint and resume (the review's recommendation)
Persist message history, tool-call state, and iteration count after each step; on restart,
reload and continue.
**Rejected for now.** The hard part is not persistence, it is **side-effect idempotency**.
Resuming after a tool call whose result was not recorded means either re-executing it
(duplicate writes) or skipping it (lost work), and distinguishing the two requires every
tool to be idempotent or transactionally journalled. That is a large, invasive change
across all ten builtins for a benefit — saving a retry on an uncommon event — that does not
justify it at current scale.

Revisit if unattended long-running agents become the dominant workload, or if runs routinely
exceed several minutes.

### Route all runs through RQ
Interactive chat also goes through `frappe.enqueue`, giving uniform durability.
**Rejected.** It reintroduces a fixed worker pool — the exact ceiling
[ADR 0001](0001-agno-fastapi-over-frappe-native.md) exists to remove — and breaks streaming,
since the process producing tokens would no longer be the one holding the browser's SSE
connection. That would force either Redis pub/sub relay or polling, both of which
[ADR 0004](0004-sse-direct-from-fastapi.md) rejected.

### External workflow engine (Temporal, Restate)
Purpose-built durable execution with correct replay semantics.
**Rejected as disproportionate.** It adds a major piece of infrastructure to a Frappe bench
for one workload. Worth reconsidering only if durable execution becomes a hard requirement
across several features rather than a nice-to-have for one.

---

## Verification

- Kill the FastAPI service mid-run → the run is marked `Failed` within
  `RUNNING_STALE_SECONDS`; the UI reflects the failure and the session remains usable.
- Reload a session with an orphaned `Running` run → `recover_session` fails it and reports
  the count.
- Retry after a failure → a new run starts with prior conversation history intact.
- Kill an RQ worker mid-trigger → the job is redelivered and the trigger completes exactly
  once (the condition is re-evaluated in `fire`, guarding against state drift).
- No run remains `Running` indefinitely under any kill scenario.

---

## References

- [001 — Architecture §9](../specifications/001-architecture.md) — failure handling table
- [ADR 0001 — Agno + FastAPI](0001-agno-fastapi-over-frappe-native.md)
- [ADR 0004 — SSE direct from FastAPI](0004-sse-direct-from-fastapi.md)
- [Progress tracker — Phase 8](../progress/flow-to-frappe-ai-migration.md)
