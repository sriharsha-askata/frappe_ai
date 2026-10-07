# ADR 0020 — Retire `AI Tool` in favour of Assistant Core (FAC) tools

**Status:** Accepted — code complete; per-site rollout pending
(see [progress](../progress/ai-tool-retirement-via-assistant-core.md))
**Date:** 2026-10-06
**Deciders:** Sri Harsha Dabbiru

---

## Context

Local agent tools were defined twice. `AI Tool` rows (Imported callable or database-stored
Script code) were bound per agent through `AI Agent Tool` / `AI Agent Tool Config`, and
resolved by `lib/resolver.py` and `dispatch_tool()`. Assistant Core already provides a
version-controlled `BaseTool` implementation, a `FAC Tool Configuration` policy record, and
a `ToolRegistry` that repeats enablement, role, permission, argument and audit checks.

Running both meant two names, descriptions, enabled flags, confirmation policies and
authorization paths per tool that could drift, and a confirmation-approval path that once
resolved a FAC tool to the legacy row and skipped FAC's role checks
(production-readiness finding F-7b).

## Decision

1. The only local tool path is
   `BaseTool` (owning app) → `FAC Tool Configuration` → `AI Agent Plugin Tool`
   (per-agent selection and confirmation override) → `dispatch_plugin_tool` → `AgentBuilder`.
2. `AI Tool`, `AI Agent Tool`, `AI Agent Tool Config`, `AI Agent.tools` and
   `AI MCP Tool.matched_ai_tool` are deleted, along with the resolver and the seeded
   builtin `AI Tool` rows.
3. No alias or link from `AI Tool` to FAC is kept; matching during migration is by exact
   `slug` = `FAC Tool Configuration.tool_name`.
4. Deletion is gated per site: a private archive of every legacy row (owner and
   verification reference per slug, checksummed) must exist and match the live data, no
   run may be `Running`/`Paused`, and every active legacy binding must have an enabled FAC
   binding with the same confirmation policy. `patches/assert_ai_tool_retirement_ready.py`
   enforces this before model sync; `patches/delete_ai_tool_doctypes.py` removes the
   DocTypes after it; `api/retirement.drop_retired_tables` drops the physical tables and
   the obsolete column (Frappe deliberately keeps orphan tables).
5. The sandboxed `safe_exec` namespace ([ADR 0006](0006-unified-safe-exec-namespace.md))
   is retained for `execute` and `AI Trigger.condition`; Script `AI Tool` rows no longer exist.

## Rejected alternatives

- **Permanent `AI Tool` → FAC link/alias:** keeps two sources of truth for name,
  description, enabled state and confirmation policy.
- **Auto-converting `AI Tool.import_path` into FAC `module_path`:** a callable path is not a
  registered `BaseTool` class; conversion must be a reviewed rewrite.
- **Auto-migrating Script `code` into FAC:** would execute database-stored code through a
  different trust boundary. Every Script row needs a reviewed, versioned replacement or a
  recorded retirement.
- **Keep the DocTypes as read-only compatibility records:** leaves schema, controllers and
  docs describing a system that no longer runs.

## Consequences

- Existing sites cannot migrate until archived and classified. A fresh install needs no archive.
- Rollback after the deletion release is a restore of the pre-deletion database and private
  files together with the previous app release.
- The DocType controller shims, both patches, `api/retirement.py`, `api/migration.py`, their
  tests and the runbook are temporary **retirement machinery**. They are removed in a second
  stage once every site has migrated and the rollback window has ended, leaving only this ADR
  as the historical record.
- Still open and independent of this decision: MCP calls bypass the ADR 0008 budget counters,
  the `execute` vs Assistant Core `run_python_code` comparison, `run_action` parity, and
  end-to-end verification of the Spec Review, Historical Match and CAD workflows.
