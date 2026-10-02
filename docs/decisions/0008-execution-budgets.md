# ADR 0008 — Per-run limits on what an agent may do

**Status:** Accepted. Implemented for direct Frappe and Assistant Core tool calls; not yet for MCP calls (see below). · **Date:** 2026-08-05

## The problem

Permissions ([ADR 0003](0003-tools-execute-in-frappe.md)) say *what* an agent may touch. They say nothing about *how much*. The only other limit, `max_iterations`, bounds the reasoning loop, not the work done inside it. In one turn an agent can call `create` with a huge list, update or delete across many names, or make many tool calls at once. Two situations make this real:

- **Unattended runs** (`auto_approve` triggers) have no human approving each step, so nothing else holds them back.
- **Prompt injection:** a document or knowledge chunk saying "delete every draft order" is limited by the user's permissions only, not by volume.

## The decision

Give every run **budgets**, set per agent and enforced in Frappe.

| Field on `AI Agent` | Default | What it limits |
|---|---|---|
| `max_tool_calls` | 50 | Tool calls in one run |
| `max_mutations` | 20 | Calls that change data per run |
| `max_records_per_call` | 100 | Records touched by one call |
| `max_runtime_seconds` | 600 | Active time per **segment** (run start, or the latest resume). Time spent Paused waiting for approval does not count |

`max_iterations` stays; it limits a different thing. The budgets are copied into the run's `config_snapshot` when it starts.

**Where they are enforced:** in Frappe's dispatch endpoints (`frappe_ai/api/dispatch.py` calling `api/budgets.py::consume`) before a tool runs, right where permissions are checked. Every dispatch must name a live run owned by the user, so budgets cannot be skipped by leaving the run out. The service is never the authority, because it is the part assumed to be possibly compromised.

**What counts as a change:** calls to tools named `create`, `update`, `delete`, `run_action`, `create_document`, `update_document`, `delete_document` or `run_workflow`. Other tools that change data are not counted unless added to the list in `dispatch.py`. Reads count only toward `max_tool_calls`. Records are counted from the call's arguments (`records`, `documents` or `values` lists).

**Counters** are stored on the run in `AI Run.budget_usage`, for example `{"tool_calls": 12, "mutations": 3, "records": 47}`. Storing them in Frappe rather than service memory means they survive a resume (a paused and resumed run keeps counting), survive a service restart, and can be audited afterwards.

**When a limit is hit** the tool call is refused with an error naming the budget; nothing is silently cut down. Earlier changes in the run stay committed, because there is no transaction across calls.

## Known gaps

- **MCP calls are not counted.** The service talks to MCP servers directly, so their calls never pass through Frappe's dispatch. Do not give an unattended agent MCP tools that change data.
- **Counters are read-then-written without a lock.** Parallel tool calls in one run could slightly exceed a limit.
- The list of "changing" tools is by name, not by what the tool actually does.
- Until rate limiting, heartbeats and similar hardening are done ([010](../specifications/010-review-topics.md)), avoid `auto_approve` triggers and unbounded MCP mutations against production data.

## What follows

**Good**
- Prompt injection, a buggy agent or a runaway trigger can do a bounded amount of damage.
- Per-agent tuning: a cautious customer-facing agent and a bulk-cleanup agent can differ widely.
- `max_tool_calls` and `max_runtime_seconds` also cap model spend.
- `budget_usage` shows what each run actually did.

**Costs**
- Legitimate bulk work (say a 500-record import) needs the limits raised or the work split.
- A run that hits `max_mutations` mid-batch leaves earlier changes in place.
- Four more fields on `AI Agent`.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Confirmation alone | Fails for `auto_approve` runs, and approving "create 500 records" from a one-line summary is not a real review |
| One global limit in `AI Settings` | Agents legitimately differ by an order of magnitude |
| Count only in the service | The service is the component whose compromise we assume possible, so the limit would only be advice |
| Silently truncate batches | The agent would report success for work that did not happen |

## How we check

- A call representing 500 records is refused at `max_records_per_call`.
- A paused and resumed run keeps counting.
- A budget is enforced when a tool is dispatched directly, bypassing the service.
- A read-heavy run is not stopped by `max_mutations`.
- A segment running past `max_runtime_seconds` is refused; a run resumed after a long approval wait is not.
- `budget_usage` matches the tool calls recorded on the run.

## Related

[ADR 0003](0003-tools-execute-in-frappe.md), [ADR 0007](0007-failure-over-durable-execution.md), [003 AI Agent and AI Run](../specifications/003-doctype-reference.md).
