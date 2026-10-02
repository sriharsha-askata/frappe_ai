# ADR 0003 — Tools run inside Frappe, as the user who started the run

**Status:** Accepted · **Date:** 2026-08-05 · **Updated:** 2026-10 (dispatch is bound to the run; approvals enforced in Frappe)

## The problem

After moving the agent loop into a separate service ([ADR 0001](0001-agno-fastapi-over-frappe-native.md)), where should *tools* run? Tools read and change Frappe data (`read`, `create`, `update`, `delete`, `run_action`, `execute`, and tools from other apps). The app's security rests on them running as the requesting user: they use `frappe.has_permission` and permission-aware calls such as `frappe.get_list` (never `frappe.get_all`), and `execute` runs in a restricted sandbox.

An alternative design has the service hold Frappe API credentials and call the REST API.

## The decision

**Every tool that touches Frappe data runs inside Frappe, as the user who started the run.**

The service only:
1. receives each tool's JSON Schema (no code),
2. lets the model choose a tool and arguments,
3. asks a Frappe endpoint to run it (`dispatch_plugin_tool`, or `dispatch_tool` for legacy `AI Tool` rows),
4. gives the result back to the model.

What the dispatch endpoint (`frappe_ai/api/dispatch.py`) does on every call:

1. **Authenticates the service** with the shared secret (`X-Frappe-AI-Service-Secret`).
2. **Binds the call to a live run.** The call must name a run that exists, is *Running* or *Paused*, and **belongs to the acting user**. Without a `run` it is refused.
3. **Enforces approval.** If the tool needs confirmation, it runs only when the user's approval for *that call id, that tool and those exact arguments* is on record on the run (written by `resume_run`, used once). Runs started with `auto_approve` skip this. A tool the agent was never given is refused, apart from the run-scoped internal tools (`search_knowledge`, `update_memory`, `load_full_document_text`).
4. **Counts the call against the run's budgets** ([ADR 0008](0008-execution-budgets.md)).
5. **Switches to the user** (`frappe.set_user`), runs the tool with all permission checks, and restores the previous user in a `finally` block.
6. Returns the result, or `{"error": "..."}` (cut to 500 characters) if the tool raised.

### The invariant

> The service can never cause an action that the acting user could not have done themselves in the Desk.

### What it does not cover

- **How much.** A permitted user can still be steered (by prompt injection or a bug) into many legitimate-looking changes. Budgets and approvals address this.
- **A leaked shared secret.** Because every call must name a live run owned by the named user, the secret alone cannot act as an arbitrary user (for example Administrator). But an attacker holding it can act as any user who *currently has an active run*, within that run's limits. Keep the secret private and the service port closed. A per-run credential for service-to-Frappe calls would remove this residual risk (see [010](../specifications/010-review-topics.md)).
- **MCP tools.** They run in the service against an external server, outside this boundary ([007](../specifications/007-mcp-integration-and-cleanup.md)).

## What follows

**Good**
- Roles, user permissions, `if_owner` and DocType rules apply exactly as in the Desk.
- A compromised service is limited to users with live runs, not Administrator.
- `safe_exec` stays in the process that owns the data.
- Prompt injection cannot escalate privileges: at worst it makes the agent try something the user could already do.
- Frappe's version history attributes changes to the real user.
- Tool code is written once, in Frappe.

**Costs**
- One HTTP round trip per tool call (milliseconds on localhost, small next to model time).
- Dispatch briefly occupies a Frappe worker, but for milliseconds, not for the whole run.
- The dispatch endpoint is security-critical and needs careful review.
- Failures span two logs; run ids help correlate.

Tools that do not touch Frappe data (pure computation, external HTTP) may run in the service.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Service calls Frappe's REST API as one service user | Everyone's agent would act with that one broad identity: users could read data they cannot see, prompt injection would inherit its permissions, the sandbox would be lost, and the audit trail would blame the service account |
| Service imports `frappe` directly | Couples the service to the site's database and filesystem, Frappe's database layer is not async-safe (it would block the loop again), and multi-site becomes costly |
| A signed token per tool call | The decision and the action still happen in Frappe, so it adds ceremony without changing where the work happens |

## How we check

- A user without permission on a DocType asks the agent to read it: the tool fails and the model is told so.
- `create`, `update`, `delete` are refused for users without those permissions.
- `execute` cannot reach `frappe.db.sql` or `frappe.get_all`.
- A dispatch with a missing, finished, unknown or other-user run is rejected.
- A dispatch without a valid secret is rejected.
- A confirmation tool is rejected without a matching approval, rejected a second time, and rejected if its arguments changed.

Tests: `frappe_ai/tests/test_api.py`.

## Related

[001 §5](../specifications/001-architecture.md), [ADR 0006](0006-unified-safe-exec-namespace.md), [ADR 0008](0008-execution-budgets.md).
