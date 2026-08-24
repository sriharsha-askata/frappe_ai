# ADR 0008 — Execution budgets and mutation limits

**Status:** Accepted — direct Frappe/FAC dispatch implemented; remote MCP coverage and
production-hardening verification remain deferred
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru
**Prompted by:** production-readiness review (tool execution budgets, mutation limits)

---

## Context

`flow` bounds agent runs with exactly one control: `max_iterations` (default 10, 40 for the
built-in assistant). Exceeding it raises and fails the run.

That bounds the **loop**, not the **work**. Within a single iteration an agent may:

- call `create(doctype, records)` with an unbounded list of records
- call `update(doctype, names, values)` across an unbounded set of names
- call `delete(doctype, names)` likewise
- issue many tool calls in one assistant turn (parallel tool calling)

So a 10-iteration cap permits an effectively unlimited number of writes. Two things make
this more than theoretical:

1. **`auto_approve = 1` on triggers.** Confirmation is the human check on mutating tools.
   Unattended trigger runs bypass it by design — correctly, since nobody is there to
   approve — leaving no backstop at all.
2. **Prompt injection.** [ADR 0003](0003-tools-execute-in-frappe.md) guarantees an agent
   cannot exceed the acting user's *permissions*. It does not bound how much damage a user
   with legitimate write permission can be induced to do. A knowledge chunk or document
   containing "delete every draft order" is constrained only by permissions, not by volume.

`flow` carries this gap. Porting it unchanged would be a mistake.

---

## Decision

Introduce **per-run execution budgets**, configured on `AI Agent` and enforced in Frappe.

### Budget fields (on `AI Agent`)

| Field | Default | Bounds |
|---|---|---|
| `max_tool_calls` | 50 | Total tool invocations per run |
| `max_mutations` | 20 | `create` + `update` + `delete` + `run_action` calls per run |
| `max_records_per_call` | 100 | Records touched by a single mutating call |
| `max_runtime_seconds` | 600 | Wall-clock; the run fails past this |

`max_iterations` is retained — it bounds a different thing (reasoning depth) and the two are
complementary.

### Enforcement — current boundary and remaining gap

1. **At the direct Frappe dispatch boundary** (`frappe_ai/api/dispatch.py`) — the per-run
   counters are checked and incremented before native or direct FAC tools execute. This is
   the authoritative gate, in the same place permission checks happen.
2. **Remote MCP is a known gap.** An independently executing MCP transport currently does
   not pass through `frappe_ai/api/dispatch.py`, so its calls do not receive these counters.
   A separate propagation/design step is required before remote MCP mutations are treated
   as budget-equivalent.

The direct boundary counts the records represented by the dispatch argument payload. The
implementation does not yet provide a second per-builtin enforcement layer for every
possible tool implementation; that remains part of the hardening follow-up.

### Accounting

Counters live in a new `AI Run.budget_usage` (JSON) field:

```json
{"tool_calls": 12, "mutations": 3, "records": 47}
```

Persisting on `AI Run` rather than in service memory gives three properties:

- **Resume-safe.** A paused-then-resumed run continues accumulating rather than resetting —
  the same reasoning that makes `apply_result` accumulate iterations and usage.
- **Auditable.** Post-hoc "how much did this run actually do?" is answerable.
- **Survives service restart.** Consistent with
  [ADR 0007](0007-failure-over-durable-execution.md), where the run fails but its record
  remains truthful.

### On exceeding a budget

The run **fails** with an explicit error naming the budget. It does not silently truncate.
A partially-applied batch is reported through the existing `failures` mechanism, so the user
can see exactly what landed.

---

## Consequences

### Positive

- **Bounded blast radius.** Prompt injection, a buggy agent, or a runaway trigger can now
  do a bounded amount of damage rather than an unbounded amount.
- **Strengthens ADR 0003.** That ADR bounds *what* an agent may touch; this bounds *how
  much* on the direct Frappe/FAC path. Remote MCP remains an explicit hole until its
  counters are propagated.
- **Per-agent tuning.** A cautious customer-facing agent and a bulk data-cleanup agent can
  have very different budgets without a global compromise.
- **Cost control.** `max_tool_calls` and `max_runtime_seconds` also cap the token spend of
  a pathological run.
- **Auditable.** `budget_usage` makes actual consumption visible per run.

### Negative

- **Legitimate bulk work needs raised limits.** A genuine 500-record import requires
  raising `max_records_per_call` or chunking across calls. Defaults are chosen for safety,
  not convenience, and administrators will hit them.
- **A failed run may leave partial writes.** Frappe has no cross-call transaction here, so
  hitting `max_mutations` mid-batch leaves earlier writes committed. Mitigated by reporting
  precisely what succeeded, not by rollback.
- **More state to thread.** Counters must reach the dispatch endpoint on every call and
  persist correctly across resume.
- **Four more fields on `AI Agent`.** Modest additional configuration surface.

### Neutral

- Read-only tools (`read`, `describe`, `find_doctypes`, `search_knowledge`) count toward
  `max_tool_calls` but not `max_mutations`. Reads are cheap and bounded by their own
  `limit` caps (200 records).

---

## Alternatives Considered

### Rely on `requires_confirmation` alone
The existing model: mutating tools pause for human approval.
**Rejected** because it fails in exactly the case that most needs a control — `auto_approve`
trigger runs — and because a human approving "create 500 records" from a summary line is not
meaningfully reviewing it.

### Global limits in `AI Settings` instead of per-agent
Simpler: one set of numbers site-wide.
**Rejected.** Agents legitimately differ by an order of magnitude in expected volume. A
global limit is either too tight for bulk agents or too loose for conversational ones.
Per-agent defaults can still be seeded from settings if that proves convenient.

### Enforce only in the FastAPI service
The service already tracks the loop, so counting there is natural.
**Rejected** for the same reason as [ADR 0003](0003-tools-execute-in-frappe.md): the service
is the component whose compromise the design assumes might happen. A budget enforced only
there is advisory. Frappe is the authority.

### Truncate instead of fail
Silently cap the batch at the limit and continue.
**Rejected.** Silent truncation means the agent believes it created 500 records when it
created 100, and will report success. Failing loudly is the honest behaviour.

---

## Implementation Note

The direct Frappe/FAC implementation landed during the Assistant Core migration. Remote
MCP coverage, SSE heartbeats, rate limiting, retries, and production verification remain
scheduled for **Phase 8.1**. This is not an assessment that the risk is low. Until the
Phase 8.1 gate completes, `frappe_ai` should not be exposed to production traffic — in
particular, `auto_approve` triggers and unbounded remote MCP mutations should not be
enabled against production data.

---

## Verification

- Direct dispatch of a call representing 500 records is stopped at `max_records_per_call`;
  the run fails with an error naming the budget and `budget_usage` is updated.
- A run that pauses for confirmation and resumes **continues** accumulating counters rather
  than resetting them.
- A budget is enforced when a native or direct FAC tool call is issued against the dispatch
  endpoint, bypassing the service.
- Remote MCP budget equivalence remains unverified and unresolved.
- A read-heavy run is not blocked by `max_mutations`.
- Exceeding `max_runtime_seconds` fails the run and releases the stream.
- `budget_usage` on a completed run matches the tool calls recorded in `AI Run.tool_calls`.

---

## References

- [ADR 0003 — Tools execute in Frappe](0003-tools-execute-in-frappe.md) — the permission
  boundary this complements
- [ADR 0007 — Fail-and-retry](0007-failure-over-durable-execution.md) — why counters persist
  on `AI Run`
- [003 — DocType Reference](../specifications/003-doctype-reference.md) — `AI Agent`, `AI Run`
- [Progress tracker — Phase 8.1](../progress/flow-to-frappe-ai-migration.md)
