# Remote MCP tool calls bypass execution budgets entirely

**Type:** Bug / Security gap (acknowledged, unresolved)
**Severity:** High
**Status:** Open — needs a design decision before implementation (see Decision 3)
**Source:** Architecture review, 2026-09-06 (Finding H-1 / Decision 3); originally
named in [ADR 0008](../decisions/0008-execution-budgets.md)

---

## Description

Per-run execution budgets (`max_tool_calls`, `max_mutations`, `max_records_per_call`,
`max_runtime_seconds`, configured on `AI Agent`) are enforced at the direct
Frappe/FAC dispatch boundary only — `frappe_ai/api/dispatch.py`, via
`frappe_ai/api/budgets.py`'s counters.

No call site in the MCP tool-construction or invocation path
(`service/builder.py:_build_mcp_tools`, `frappe_ai/api/mcp.py:_build_toolkit`)
touches a budget counter. An agent with `mcp_connections` bound therefore has **no
enforced ceiling** on tool calls or mutations made via MCP, regardless of its
configured budget fields.

## Why it needs to be done

This is not a hidden defect — the project's own [ADR 0008](../decisions/0008-execution-budgets.md)
names this gap explicitly and marks it a "Phase 8.1" hard gate before production
traffic. This review's trace confirms the gap is still fully open: nothing closes
it partially or otherwise.

The risk is concrete, not theoretical: `auto_approve` triggers already bypass human
confirmation by design (nobody is watching an unattended run). Combined with an
MCP-bound trigger agent, there is currently **neither a human check nor a volume
cap** on mutating calls made through MCP — the two backstops the rest of the system
relies on (confirmation, budgets) are both absent on this one path.

## Options considered

- **A — Pre-invocation checkpoint.** Route MCP tool calls through the same budget
  check `dispatch.py` already performs, before the call reaches the MCP transport.
  Stronger guarantee, consistent with how every other tool is bounded; may require
  restructuring where MCP calls are issued if they don't currently round-trip
  through Frappe before executing.
- **B — Service-side counter, Frappe-verified.** If MCP tools execute entirely
  within the FastAPI/Agno process without a Frappe round-trip, have the service
  maintain its own counter and report it to Frappe via the existing
  `persist_run_result` callback, enforcing on the next call. Easier to add, but
  inherently after-the-fact for the call that first trips the limit.

## Recommendation

**Update (production readiness review, 2026-09-10):** the open question below has
been resolved. `service/builder.py:_build_mcp_tools` constructs `MCPTools` that
execute **entirely inside the FastAPI process** — no MCP transport variant
round-trips through Frappe at any point, and there is no `frappe.set_user` on the
path. Option A therefore cannot be implemented as a check at the existing dispatch
boundary, because MCP calls never reach that boundary; it would require routing
MCP traffic back through Frappe, which
[ADR 0019](../decisions/0019-mcp-acting-user-identity.md) considered and rejected
as disproportionate.

**Option B is the applicable design.** The service maintains its own counter for
MCP calls and reports it to Frappe through the existing `persist_run_result`
callback, enforcing on the next call. This is after-the-fact for the single call
that first trips the limit, which is an accepted weakening — bounded overrun by
one call is materially different from today's unbounded behaviour.

This item also became more load-bearing: ADR 0019 accepts that MCP tools act as a
shared connection identity rather than the acting user, which makes the volume
ceiling the **only** remaining control on that path. Requirement 3 of that ADR
depends on this item.

**Do not bundle this with any other to-do item** — it touches the
security-critical dispatch boundary and deserves its own focused review pass,
ideally landing behind a settings-level toggle so it can be rolled back
independently.

### Original open question (now answered)

## What happens if we do nothing

The gap stays exactly where ADR 0008 already places it: a named, hard gate before
production traffic. Nothing gets worse by waiting, but the project's own docs
already say `frappe_ai` should not carry production traffic or run `auto_approve`
triggers against production data until this closes.

## Verification (once implemented)

- Direct dispatch of a call representing more records than `max_records_per_call`
  is stopped and `budget_usage` is updated (already true for the direct path;
  extend the same assertion to MCP).
- An MCP-bound agent hitting `max_mutations` via MCP fails the run with an explicit
  error naming the budget, not a silent truncation.
- `budget_usage` on a completed run that used MCP tools reflects those calls.
