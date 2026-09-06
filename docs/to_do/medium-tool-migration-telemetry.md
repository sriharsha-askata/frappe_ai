# No telemetry distinguishing legacy vs. FAC tool-call volume

**Type:** Improvement (migration governance / codebase cleanup)
**Severity:** Medium
**Priority:** Do early — precedes other cleanup so the tool-migration picture stays
current while other to-do items land
**Status:** Open — approved, not yet implemented
**Source:** Architecture review, 2026-09-06 (Finding M-3 / Decision 6)

---

## Description

Two tool-execution mechanisms are simultaneously live per agent:

- The legacy `AI Tool` / `AI Agent Tool` DocType-driven path
  (`frappe_ai/tools/builtins.py`, dispatched via `dispatch_tool`).
- The newer Assistant Core/FAC plugin path
  (`frappe_ai/assistant_tools/native.py`, dispatched via `dispatch_plugin_tool`).

The only routing logic between them is a single string check —
`tool_cfg.get("source") == "fac"` — in `service/builder.py:209`. No log line,
metric, or counter anywhere distinguishes how much live traffic is currently
flowing through each path.

## Why it needs to be done

`docs/progress/ai-tool-retirement-via-assistant-core.md` already lays out a
detailed, gated, 7-phase retirement plan for the legacy path, with explicit
rollback provisions — this is not a request to redesign that plan. But its final
deletion phase depends on confidence that no production workflow still depends on
the legacy path, and today that confidence can only come from a manual
configuration audit (checking which agents have which tool bindings), not from
observed production behavior. That is a materially weaker basis for an
irreversible deletion than actual usage data.

Flagged to be addressed **before** other cleanup items in this to-do list: the
codebase is actively mid-migration on this exact seam, and other refactors here
(e.g. `high-chatpy-confirmation-extraction.md`) touch adjacent dispatch code. Doing
this first keeps the legacy-vs-FAC picture current and observable while the rest
of the cleanup proceeds, rather than adding more untracked drift on top of an
already-untracked migration.

## Fix

Add a lightweight counter or log tag per dispatch call, keyed by source
(`"legacy"` vs. `"fac"`). The source distinction already exists in code
(`tool_cfg.get("source")`) — this only makes it observable over time, e.g. via a
counter incremented in `dispatch_tool`/`dispatch_plugin_tool`, or a structured log
field.

## Trade-offs

Small addition to an already-well-designed plan — this doesn't replace or compete
with the existing retirement plan, it strengthens its final gate.

## Migration impact

None to the retirement plan's structure. Purely additive instrumentation
alongside dispatch calls that already exist.

## What happens if we do nothing

Deletion still proceeds on the existing plan's own terms — configuration audit
plus manual verification evidence — just without the added confidence that comes
from having actually observed zero legacy-path traffic before deleting the legacy
path.
