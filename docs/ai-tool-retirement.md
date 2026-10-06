# AI Tool retirement runbook

The application now uses Assistant Core `AI Agent Plugin Tool` bindings for local
tools. `AI Tool`, `AI Agent Tool`, and `AI Agent Tool Config` are removed by the
retirement release. `AI MCP Tool.matched_ai_tool` and `AI Agent.tools` are removed
with them. `AI FAC Tool` is a separate mirror and remains unchanged.

## Before deploying the deletion release

Repeat these steps for **every installed site** while the old DocTypes are still
available. Keep the site in maintenance mode during final inventory, archive,
and migration so no agent or tool configuration changes after the archive.

1. Take a full database and private-files backup. Record its location and test
   that it can be restored.
2. Run `bench --site SITE execute frappe_ai.api.retirement.report`. This prints
   tool names, types, and bindings without printing Script code. Classify every
   slug as `migrated` or `retired`. A Script tool needs a reviewed, versioned FAC
   replacement before it can be classified as migrated; no Script code is
   converted automatically.
3. Run `bench --site SITE execute frappe_ai.api.migration.migrate_ai_tools`.
   Review `unmatched`, `ambiguous`, and `conflicts`; fix every active binding.
   The migration is safe to repeat and leaves legacy records in place.
4. Verify direct FAC permissions, confirmation, budgets, and audit results for
   each active tool family. For tender agents, run known-good Spec Review,
   Historical Match, and CAD cases and compare their persisted results. Resolve
   remote MCP budget coverage before using MCP as a replacement for a retired
   local tool.
5. Finish or recover all `Running` and `Paused` AI Runs. Archive the final
   inventory with `bench --site SITE execute frappe_ai.api.retirement.archive
   --kwargs '{"dispositions":{"read":"migrated"},"evidence":{"read":{"owner":"Team","reference":"verification-report.md"}}}'`,
   replacing the example mapping and evidence with **every** slug reported for
   that site. For a retired tool, use its retirement decision as the reference.
   Copy the returned archive file and checksum to the deployment artifacts with
   the database backup. The private archive contains Script source code and
   must retain restricted access.
6. Run `bench --site SITE execute frappe_ai.api.retirement.assert_ready`.
   Repeat migration and archival whenever a tool or binding changes.

## Deploy and verify

The pre-model-sync patch refuses deletion if the archive is missing, stale, or
an active binding lacks a valid FAC replacement. After model sync, Frappe removes
the old DocType metadata; the `after_migrate` hook drops the retained physical
tables and the obsolete MCP column. A fresh site needs no archive.

After `bench --site SITE migrate`, confirm the three old DocTypes and their
tables are absent, the agent and MCP forms load, FAC tool metadata appears in
the frontend, and a direct FAC agent run completes. Keep the archive and backup
through the rollback period. If migration stops before schema deletion, correct
the reported gate and rerun; if a deployed capability fails, restore the
pre-deletion database and private files with the previous app release.
