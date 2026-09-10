# ADR 0019 — MCP tools act as the connection, not as the user

**Status:** Accepted
**Date:** 2026-09-10
**Deciders:** Sri Harsha Dabbiru
**Supersedes in part:** [ADR 0003](0003-tools-execute-in-frappe.md)'s MCP carve-out

---

## Context

[ADR 0003](0003-tools-execute-in-frappe.md) establishes the invariant that carries
this app's entire security model:

> The FastAPI service can never cause an action the acting user could not have
> performed themselves in the desk.

It holds that invariant by executing every Frappe-data tool inside Frappe under
`frappe.set_user(acting_user)`. It then carves out an exception:

> Tools that do **not** touch Frappe (pure computation, external HTTP, MCP tools)
> may run in the service. Only Frappe-data tools are constrained by this ADR.

That carve-out was written on the assumption that an MCP server is a *foreign*
system — something like a weather API or a vendor's search index, where "the
acting user" has no meaning because the remote system has no notion of Frappe
identity.

**That assumption no longer matches how MCP is used here.** The MCP servers this
app actually connects to are overwhelmingly Frappe: `frappe_assistant_core`'s
own MCP endpoint, and app-specific servers such as `tender_automation`'s, both
running against the same site. Their tools read and mutate Frappe data.

The mechanics make the consequence precise. `service/builder.py:_build_mcp_tools`
constructs `MCPTools` that execute **in the FastAPI process**, and authenticates
with a credential stored on the connection row:

```python
if api_key and api_secret:
    headers["Authorization"] = f"token {api_key}:{api_secret}"
```

There is no `frappe.set_user` anywhere on this path, because the call never
re-enters Frappe through `frappe_ai.api.dispatch`. Every MCP tool call is
therefore made as *the connection's* identity, for every user of every agent
bound to that connection.

This is the exact shape of the design ADR 0003 rejected on security grounds —
"FastAPI calls the Frappe REST API as a service user" — reintroduced through a
door ADR 0003 left open.

### What has already changed

Before this ADR, the situation was worse than a design gap. `AI MCP Connection`
is a System Manager-only DocType, but two whitelisted methods bypassed that:
`create_mcp_connection_from_json` inserted with `ignore_permissions=True` and no
permission check, and `check_all_mcp_connections` connected every enabled
connection — spawning a stdio connection's `command` as a subprocess — with no
permission check, on a five-minute cron.

Any authenticated user could therefore *create* the identity boundary they then
crossed. Both are now gated on `AI MCP Connection` permissions.

That fix changes the nature of the remaining problem. What is left is no longer
privilege *escalation by an arbitrary user*; it is a System Manager deliberately
configuring an integration whose calls carry a shared identity.

---

## Decision

**MCP tool calls act as the configured connection, not as the run's acting user.
This is an accepted, documented limitation of MCP connections — not an
oversight — and it is bounded by three requirements.**

1. **Configuring an MCP connection is a privileged act, enforced as one.** Every
   path that creates a connection or causes one to be connected requires
   `AI MCP Connection` permissions. A connection's credential is a service
   credential; granting an agent an MCP connection grants every user of that
   agent whatever that credential can do.

2. **Same-site Frappe tools must not be reached over MCP when a direct path
   exists.** `frappe_assistant_core` tools are available through
   `dispatch_plugin_tool`, which runs them inside Frappe under
   `frappe.set_user(acting_user)` with permissions, budgets, and audit intact.
   That path is preferred, and duplicate tool names resolve to it. MCP is for
   systems that genuinely have no in-process Frappe entry point.

3. **MCP calls are counted and audited even though they are not permission-scoped.**
   Losing per-user permissions must not also mean losing the volume ceiling.
   Budgets are the only remaining backstop on this path, and `auto_approve`
   triggers remove the human one — see
   [`to_do/high-mcp-budget-bypass.md`](../to_do/high-mcp-budget-bypass.md), which
   this ADR does not close.

### The invariant, restated

ADR 0003's invariant is now scoped explicitly:

> Every tool that executes **inside Frappe** — builtins, `AI Tool` rows, and FAC
> plugin tools — acts as the run's user and can do nothing that user could not do.
> Tools that execute **in the service** over MCP act as the connection. The
> boundary between the two is the `AI MCP Connection` permission.

---

## Consequences

### Positive

- **The security model is stated accurately.** The previous documentation implied
  a guarantee the MCP path did not provide. An operator can now reason correctly
  about what binding an MCP connection to an agent actually grants.
- **The escalation vector is closed.** The dangerous property was never the shared
  credential itself; it was that any user could create one and trigger its use.
- **The direct FAC path is the default for same-site tools**, so the common case
  keeps full per-user permission scoping.
- **No large rewrite.** Routing all MCP traffic back through Frappe would mean
  building an MCP client inside Frappe and re-entering the synchronous-DB-in-an-
  async-loop problem ADR 0001 exists to avoid.

### Negative

- **A shared identity remains on one path.** A user can, through an MCP-bound
  agent, cause reads or writes they could not perform themselves. This is a real
  reduction in guarantee versus the direct path, and prompt injection can reach it.
- **Audit attribution is weaker.** Changes made over MCP against another Frappe
  site attribute to the connection's user, not the human who prompted them.
- **Operators must understand the distinction** between plugin tools and MCP tools
  to configure agents safely — a documentation burden that did not previously exist.

### Neutral

- Genuinely external MCP servers (no Frappe identity to carry) are unaffected;
  for them a connection-level credential is the only coherent model anyway.

---

## Alternatives Considered

### Route every MCP tool call back through Frappe dispatch
The strongest option: MCP calls would carry the acting user and be budgeted like
any other tool. Rejected **for now** as disproportionate — it requires an MCP
client inside the Frappe process and re-introduces blocking I/O into request
handling. Reconsider if MCP becomes the primary tool transport rather than the
exception.

### Per-user MCP credentials (OAuth token per Frappe user)
Preserves identity properly and `frappe_assistant_core` can issue such tokens.
Rejected as premature: it requires per-user credential storage, refresh, and
revocation for every connection, and the same-site case it would fix is better
solved by not using MCP at all (requirement 2).

### Forbid MCP connections to the local site outright
Clean, and requirement 2 achieves most of it by convention. Rejected as a hard
rule because app-specific servers on the same bench (e.g. `tender_automation`'s)
are a legitimate deployment pattern, and a hard block would break them without
offering a migration path.

---

## Verification

- Creating an `AI MCP Connection` through any whitelisted method as a
  non-System-Manager is refused. *(Covered by
  `test_mcp.TestMCPConnectionAuthorization`.)*
- Triggering connection checks as a non-System-Manager is refused. *(Same.)*
- Where a tool name exists both as a FAC plugin tool and over MCP, the direct
  plugin path is the one that executes.
- An MCP-bound agent's calls appear in the run's `budget_usage` — **not yet true**;
  tracked in [`to_do/high-mcp-budget-bypass.md`](../to_do/high-mcp-budget-bypass.md).

---

## References

- [ADR 0001 — Agno/FastAPI over Frappe-native](0001-agno-fastapi-over-frappe-native.md)
- [ADR 0003 — Tools execute inside Frappe](0003-tools-execute-in-frappe.md)
- [ADR 0008 — Execution budgets](0008-execution-budgets.md)
- [001 — Architecture §4, §6](../specifications/001-architecture.md)
- `frappe_ai/service/builder.py:_build_mcp_tools` — where the connection identity is applied
