# `budgets.consume` is an unlocked read-modify-write

**Type:** Bug / Concurrency
**Severity:** High
**Status:** Open
**Source:** Production readiness review, 2026-09-10 (Finding F-12)

---

## Description

`frappe_ai/api/budgets.py:consume` reads the run, mutates counters in Python, and
writes them back:

```python
doc = frappe.get_doc("AI Run", run)
usage = ...
usage["tool_calls"] += 1
...
doc.db_set("budget_usage", json.dumps(usage), update_modified=False)
```

There is no row lock between the read and the write. Two tool calls dispatched
concurrently for the same run both read the same `budget_usage`, both increment
from it, and the second write silently discards the first's increment.

## Why it needs to be done

Budgets are the enforcement point named in
[ADR 0008](../decisions/0008-execution-budgets.md) and the backstop ADR 0003
explicitly defers to for bounding *volume* rather than *authority*:

> It bounds **what** an agent may touch, not **how much**. […] Execution budgets
> and mutation limits close that gap.

The failure is worst exactly where the control matters most. A single sequential
tool call chain is unaffected; a model issuing **parallel tool calls** — which is
normal behaviour for current models and which the Agno loop permits — is precisely
the case where a run does the most work per turn, and it is the case where the
counter loses increments.

The practical effect is that `max_tool_calls`, `max_mutations` and
`max_records_per_call` are advisory under concurrency rather than enforced. A
runaway or injected agent can exceed all three while `budget_usage` reports
compliance, so the audit record is wrong too.

This became more load-bearing after the 2026-09-10 review: with
[ADR 0019](../decisions/0019-mcp-acting-user-identity.md) accepting a shared
identity on the MCP path, budgets are the main remaining ceiling on that path.

## Fix

Lock the run row for the duration of the read-modify-write:

```python
frappe.db.get_value("AI Run", run, "budget_usage", for_update=True)
```

then mutate and write inside the same transaction. Frappe's `for_update=True`
issues `SELECT … FOR UPDATE`, which serialises concurrent consumers on the row.

Note that the existing increment-then-compare ordering is **correct** and should
not be changed: incrementing and then testing `> max` is equivalent to testing
`current + 1 > max`, so the Nth call succeeds and the N+1th is refused. The defect
is purely the missing lock.

## Trade-offs

Serialising on one row per run adds contention between parallel tool calls of the
same run. That is acceptable and arguably desirable — the alternative is an
unenforced limit — and the lock is held for a single small update, not for the
tool call itself.

## Verification

- N concurrent dispatches against a run with `max_tool_calls = N-1` result in
  exactly `N-1` successes and at least one explicit budget refusal.
- `budget_usage` after a run with parallel tool calls equals the actual number of
  calls made, with no lost increments.
- Sequential behaviour is unchanged: the limit-th call succeeds, the next fails.
