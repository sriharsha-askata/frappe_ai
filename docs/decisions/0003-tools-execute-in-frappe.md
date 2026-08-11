# ADR 0003 — Tools execute inside Frappe, not in the FastAPI service

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Context

Moving orchestration into a separate service ([ADR 0001](0001-agno-fastapi-over-frappe-native.md))
raises the question of where **tools** run — specifically the eight builtin tools that read
and mutate Frappe data (`find_doctypes`, `describe`, `read`, `create`, `update`, `delete`,
`run_action`, `execute`), plus user-authored `AI Tool` rows.

`flow`'s security model rests entirely on these tools executing in-process as the
requesting user:

- Every builtin calls `frappe.has_permission` or a permission-respecting API
  (`frappe.get_list`, never `frappe.get_all`).
- `read` uses `get_list`, which applies role permissions, user permissions, and
  `if_owner` rules automatically.
- `execute` runs in a hardened `safe_exec` namespace that **excludes** `frappe.db.sql`,
  `frappe.qb`, `frappe.db.set_value`, and `frappe.get_all`, and forces
  `user=frappe.session.user` on every `get_list` call.
- `create`/`update`/`delete`/`run_action` perform per-record permission checks.

The reference architecture specification proposes the opposite: FastAPI holds Frappe API
credentials and calls the REST API to do its work.

---

## Decision

**All tools that touch Frappe data execute inside the Frappe process, under the identity of
the user who initiated the run.**

FastAPI's role is limited to:

1. Receiving tool **schemas** (JSON Schema only, no implementation) as part of agent config.
2. Letting the LLM decide which tool to call with which arguments.
3. **Dispatching** that decision back to a Frappe whitelisted endpoint.
4. Feeding the result back into the model loop.

The dispatch endpoint:

- Authenticates the service via the shared secret in `AI Settings`.
- Receives the acting user and the run id.
- Verifies the run exists, is `Running`, and belongs to that user.
- Calls `frappe.set_user(acting_user)` before executing the tool.
- Executes the tool exactly as `flow` does, with all permission checks intact.
- Restores the prior user in a `finally` block.

### The invariant

> **The FastAPI service can never cause an action the acting user could not have performed
> themselves in the desk.**

### What this invariant does *not* cover

It bounds **what** an agent may touch, not **how much**. A user with legitimate write
permission can still be induced — by prompt injection, or by a buggy agent — to make an
unbounded number of legitimate-looking changes. `max_iterations` bounds the reasoning loop,
not the work done inside it.

Execution budgets and mutation limits close that gap, and the dispatch endpoint defined here
is their enforcement point: the same place, for the same reason, as the permission check.
See [ADR 0008](0008-execution-budgets.md). Until those ship (Phase 8.1), this app should not
carry production traffic and `auto_approve` triggers should not run against production data.

---

## Consequences

### Positive

- **The permission model survives the architecture change unchanged.** Role permissions,
  user permissions, `if_owner`, and DocType-level restrictions all apply exactly as before.
- **The blast radius of a service compromise is bounded.** An attacker controlling the
  FastAPI process can act only within the permissions of users who happen to have runs in
  flight — not as Administrator.
- **`safe_exec` keeps working.** Script tools and `execute` stay in the process that owns
  the sandbox. Moving them would have meant either shipping a sandbox to the service or
  abandoning sandboxing entirely.
- **Prompt injection cannot escalate privilege.** A malicious instruction in a document or
  knowledge chunk can at worst make the agent attempt an action the user could already
  perform. This is the single most important property in an LLM system with write access.
- **Audit correctness.** Frappe's own `Version`/`Comment` records attribute changes to the
  real user, not a service account.
- **Tool code is written once.** Builtins port from `flow` essentially unchanged.

### Negative

- **Latency.** Each tool call is an HTTP round-trip service→Frappe. A 10-iteration run with
  one tool call per iteration adds 10 round-trips. On localhost this is single-digit
  milliseconds each and negligible against LLM latency, but it is not free.
- **Frappe workers are still involved.** Tool dispatch briefly occupies a worker. The
  crucial difference from `flow` is *duration*: a permission-checked `get_list` takes
  milliseconds, where an LLM call takes seconds. The worker is no longer held for the whole
  run.
- **A new authenticated endpoint.** The dispatch endpoint is security-critical and must be
  carefully written and reviewed.
- **Two-process debugging.** A failing tool call spans both logs; correlation ids are
  mandatory.

### Neutral

- Tools that do **not** touch Frappe (pure computation, external HTTP, MCP tools) may run
  in the service. Only Frappe-data tools are constrained by this ADR.

---

## Alternatives Considered

### FastAPI calls the Frappe REST API as a service user (the reference specification's design)
**Rejected on security grounds.** The service would authenticate as a single API user,
which for the builtins to work at all would need broad permissions across many DocTypes.
Consequences:
- Every user's agent would act with that shared identity — per-user permissions gone.
- A user could ask the agent to read data they cannot see. The agent would comply.
- Prompt injection would escalate to the service user's full permissions.
- `safe_exec` sandboxing would be lost or reimplemented.
- Audit trails would attribute every change to the service account.

This is not a theoretical concern: `read` returning rows the user cannot see is the
expected default behaviour of that design, not an edge case.

### FastAPI imports `frappe` directly (`frappe.init` / `frappe.connect` / `frappe.set_user`)
Would preserve permissions and remove the HTTP hop. Rejected because:
- It couples the service to the site's filesystem and DB, forfeiting independent deployment.
- Frappe's DB layer is synchronous and not async-safe; mixing it into an async event loop
  reintroduces blocking — the problem this migration exists to solve.
- Multi-site support becomes `frappe.init` per request, which is expensive and error-prone.

Reconsider only if tool-dispatch latency proves material in production.

### Signed capability tokens per tool call
Frappe mints a token encoding exactly which operation is permitted; the service redeems it.
More cryptographically rigorous, but the permission decision still has to be made in Frappe
and the operation still has to execute there — so it adds ceremony without changing where
the work happens. Rejected as over-engineering.

---

## Verification

The decisive test, included in the end-to-end suite:

> As a user **without** System Manager and **without** read permission on a chosen DocType,
> ask the agent to read that DocType. The tool must fail closed and the model must be told
> it lacks permission.

Additionally:
- `create`/`update`/`delete` are refused for users lacking those permissions.
- `execute` cannot reach `frappe.db.sql` or `frappe.get_all`.
- Dispatch requests carrying a mismatched user/run pair are rejected.
- Dispatch requests without a valid service secret are rejected.
- Frappe `Version` records attribute agent-made changes to the real user.

---

## References

- [001 — Architecture §4, §6](../specifications/001-architecture.md)
- [ADR 0006 — Unified safe_exec namespace](0006-unified-safe-exec-namespace.md)
- `apps/flow/flow/tools/builtins.py` — the permission-checking tool implementations
- `apps/flow/flow/utils/safe_exec.py` — the hardened namespace
