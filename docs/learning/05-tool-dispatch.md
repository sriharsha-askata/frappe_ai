# Lesson 5 - Tools and dispatch (`api/dispatch.py`)

Also: `api/budgets.py` (`consume`). Related: `assistant_tools/` (Assistant Core / FAC tools).

## Why it exists
The model only emits text asking for a tool. `dispatch_plugin_tool` is the one place in Frappe where such a request becomes a real action, and it is the security boundary of the whole system (ADR 0003): the FastAPI service is not trusted to decide anything.

## Concepts
- **Assistant Core (FAC) tool registry**: tools registered in Frappe; `registry.execute_tool(name, args)` runs one.
- **Acting user**: the call runs under the run owner's identity, so normal Frappe permissions apply. A compromised service can do no more than that user could.
- **Defence in depth**: the service already gates confirmation (Lesson 3), but Frappe re-checks everything.

## The checks, in order (`dispatch_plugin_tool`)
1. `_verify_service_secret` (header, constant-time compare). The endpoint is `allow_guest=True`; this is the lock.
2. The user exists.
3. `_require_active_run(run, user)`: run given, exists, status `Running`/`Paused`, `owner == user`. Binds the call to a live run of that user.
4. `_require_plugin_binding(run, tool)`: the run's agent must have an enabled `plugin_tools` row for the tool, unless it is run-scoped (`search_knowledge`, `update_memory`, `load_full_document_text`). Binding decides whether the agent may use a tool at all, so `auto_approve` can never authorise an unbound registry tool.
5. `_enforce_confirmation(...)`: if the binding requires confirmation and the run snapshot has no `auto_approve`, the call needs a recorded approval, consumed once (`consume_approval` matches run, call id, tool and argument hash). `resume_run` (user-authenticated) records approvals; the service cannot.
6. Inside `_acting_user_context(user)`: look up the tool in the registry; decide if it is mutating (`_plugin_tool_is_mutating`: category from `FAC Tool Configuration`, anything other than `read_only` counts); `consume(run, mutation, records)` charges the budget.
7. Inject server-owned context the model can never choose: knowledge bases for `search_knowledge`; agent and source run for `update_memory`; source run and model for `load_full_document_text` (`_resolve_plugin_context`).
8. `registry.execute_tool`. Exceptions are logged to Error Log with the exact arguments and returned as `{"error": <=500 chars}` so the model can see and adapt. Success returns `{"result": ...}`.

## Budgets (`budgets.consume`)
- Fails closed with no run, an unknown run, or a non-active run.
- Locks the `AI Run` row (`for_update`) so parallel tool calls cannot overwrite each other's counters.
- Limits come from the frozen `config_snapshot`: `max_runtime_seconds` (600 default; measured from `segment_started_at`, so human approval time is not charged), `max_tool_calls` (50), `max_mutations` (20), `max_records_per_call` (100).
- Usage is stored as JSON in `AI Run.budget_usage`.
- Records per call = the longest list argument (min 1), counted only for mutations.

## Example (hypothetical)
Model asks `create_task({"subject": "Call Ana"})`, binding says confirm.
- First attempt: no approval -> `PermissionError: Tool create_task needs the user's approval`. (Normally the service pauses before reaching here.)
- After the user approves in `resume_run`, the approval (run, call_1, create_task, hash of args) is stored; the dispatch call consumes it, charges 1 tool call + 1 mutation, runs as the user; a second identical call_id cannot reuse the approval.

## Production notes
- Handled: service auth, user binding, live-run requirement, binding enforcement, single-use argument-bound approvals, per-user permissions, budgets with row lock, server-injected scope, errors truncated.
- Partial: mutation detection depends on tool category metadata (fails safe to "mutating"); budget accounting is per run only.
- Missing: remote MCP tool calls skip all of this (no approvals, no budgets); no per-tool rate limits; `auto_approve` runs have no human check (restricted to System Managers and covered by the untrusted-content guard for triggers).

## Checkpoint questions
1. Why is it not enough for the service to pause before calling dispatch?
2. What could a model do if `_require_plugin_binding` was removed and `auto_approve` was on?
3. Why are `__frappe_ai_agent` and the knowledge-base list injected by the server rather than accepted as arguments?
4. Why does `consume` lock the row, and why does an approval wait not count against `max_runtime_seconds`?
