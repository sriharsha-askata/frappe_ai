# Progress — Retire `AI Tool` / `AI Agent Tool` via Assistant Core

> Tracks [007 — MCP Integration & DocType Cleanup](../specifications/007-mcp-integration-and-cleanup.md) Parts 4-5 (the verified tool mapping and 7-phase retirement plan).
> Keep this file current as work happens.

---

## Overall Status

| | |
|---|---|
| **Status** | 🟡 Tender migration is Priority 1 |
| **Current phase** | Verify direct tender workflows before MCP removal |
| **Started** | 2026-08-19 |
| **Last audited** | 2026-08-21 |
| **Blockers** | Known-good tender workflow verification; no live model credentials for LLM-driven workflow tests |

### Phase summary

| Phase | Name | Status |
|---|---|---|
| 1 | Read-only builtins via Assistant Core | 🟢 Implemented and direct-dispatch verified |
| 2 | Mutating builtins via Assistant Core | 🟡 Direct path exists; MCP budget coverage unresolved |
| 3 | `execute` sandbox parity | 🔴 Not completed; wrapper exists, comparison/tests do not |
| 4 | frappe_ai-native tools as Assistant Core contributions | 🟡 Registered; behavior/parity tests incomplete |
| 5 | Tender-specific tools as tender_automation contributions | 🟡 Direct migration complete on tact.local; workflow verification pending |
| 6 | `run_action` and full parity audit | 🔴 Not completed |
| 7 | Retire legacy runtime authority | 🟡 Runtime use is being reduced; DocTypes are retained |

## Focused migration plan: `AI Tool` → Assistant Core/FAC

**Plan status:** 🟡 Review draft — implementation is intentionally deferred until the
checklist below is reviewed.

This section is the detailed, reviewable plan for removing the redundant legacy tool
system. [007 — MCP Integration & Cleanup](../specifications/007-mcp-integration-and-cleanup.md)
remains the broader integration plan; this section is the concrete migration and deletion
runbook for `AI Tool`, `AI Agent Tool`, and their compatibility field.

### The target decision

The final runtime should have one canonical tool path:

```text
BaseTool implementation in the owning app
        ↓
FAC Tool Configuration
        ↓
AI Agent Plugin Tool (per-agent selection)
        ↓
ToolRegistry / dispatch_plugin_tool
        ↓
frappe_ai AgentBuilder
```

`AI Tool` must not become a permanent alias or a second source of truth. During migration,
legacy rows are matched to FAC configurations by their stable tool name and retained only
as compatibility data until the cutover gates pass. The migration does **not** create an
`AI Tool → FAC Tool Configuration` link.

This is deliberate:

- `AI Tool.import_path` points to a legacy callable function; FAC `module_path` points to
  a registered Assistant Core `BaseTool` class.
- FAC owns enablement, category, role access, source app, and module metadata.
- `AI Agent Plugin Tool` owns agent selection and the per-agent confirmation override.
- Keeping a link would leave two names, descriptions, enabled flags, implementation paths,
  and confirmation policies that could drift.
- The durable post-migration relationship is the agent's
  `AI Agent Plugin Tool.fac_tool` link, not a legacy tool alias.

### Why this migration is needed

`AI Tool` currently combines two responsibilities that Assistant Core already separates:

1. A Frappe AI catalog/implementation record: imported function path or database-stored
   Script code, LLM description, enabled flag, and confirmation flag.
2. A runtime dispatch target: `dispatch_tool()` loads the row and calls `to_tool()`.

Assistant Core provides the replacement boundary:

1. A version-controlled `BaseTool` implementation registered through the `assistant_tools`
   hook.
2. A `FAC Tool Configuration` record containing runtime policy and source metadata.
3. The Assistant Core `ToolRegistry`, which repeats enablement, role, permission, argument,
   audit, and execution checks.

Maintaining both systems after migration would create duplicate tool definitions and two
different authorization/configuration paths. Removing `AI Tool` is safe only after every
consumer has moved to the FAC path and every tool without an equivalent has been given an
explicit replacement or disposition.

### What moves where

| Existing concern | Final owner | Migration action |
|---|---|---|
| Imported `AI Tool` callable | Owning app's `BaseTool` class | Wrap or refactor the callable into a registered `BaseTool`; keep business logic in the owning app. |
| Script `AI Tool.code` | Version-controlled `BaseTool` implementation, or temporary legacy path | Do not copy database code into `module_path`. Rewrite and review it, or classify it as not migratable. |
| LLM-facing description | `BaseTool.description` / FAC metadata | Compare and preserve semantics; FAC becomes canonical. |
| Global enabled state | `FAC Tool Configuration.enabled` | Migrate deliberately; do not overwrite a shared FAC setting blindly when other users/apps depend on it. |
| Tool category and role access | FAC configuration | Set and verify through Assistant Core. |
| Agent tool selection | `AI Agent Plugin Tool` | Create one row per exact migrated tool and agent. |
| Agent confirmation policy | `AI Agent Plugin Tool.requires_confirmation` | Copy the legacy effective value explicitly; do not rely on the new row default. |
| Run-time execution | `ToolRegistry.execute_tool()` via `dispatch_plugin_tool()` | Remove new runtime calls to `dispatch_tool()` after cutover. |
| Legacy migration evidence | Migration report/archive | Preserve mappings, exceptions, and verification results; no permanent alias is required. |

### Tool migration matrix

Every row must be classified before deletion. The classifications are exact replacement,
adapter required, manual rewrite, intentionally retained, or unmatched.

| Tool group | Target | Required work | Deletion gate |
|---|---|---|---|
| `find_doctypes`, `describe`, `read` | Assistant Core metadata/document tools | Confirm schema, result shape, permissions, and user filtering. | Parity tests pass for a restricted user and an allowed user. |
| `create`, `update`, `delete` | Assistant Core document mutation tools | Confirm acting-user identity, validation, confirmation, partial-failure behavior, and budgets. | Direct FAC dispatch and end-to-end confirmation tests pass. |
| `execute` | `frappe_ai` Assistant Core `BaseTool` wrapper or approved Assistant Core equivalent | Complete the `run_python_code` versus hardened `safe_exec` comparison. Keep `safe_exec` until the comparison is reviewed. | No broader namespace or permission bypass is introduced. |
| `run_action` | `frappe_ai` `BaseTool` or proven Assistant Core workflow equivalent | Compare current semantics for submit, cancel, amend, rename, workflow, and whitelisted methods. | All current action variants have explicit coverage. |
| `search_knowledge` | `frappe_ai` `BaseTool` wrapper | Reuse the existing LanceDB/MariaDB retrieval path and enforce the run's agent knowledge-base scope. | Scope-override and retrieval behavior tests pass. |
| `update_memory` | `frappe_ai` `BaseTool` wrapper | Reuse the existing memory write path; stamp agent, user scope, and source run server-side. | Model-supplied scope override tests pass. |
| Tender context, persistence, and failure tools | `tender_automation` `BaseTool` implementations | Keep tender business logic in `tender_automation`; register each tool once through hooks. | Spec Review, Historical Match, and SAP Match complete through direct FAC. |
| Tender MCP-only tools | `tender_automation` `BaseTool` implementations | Retain MCP only as migration fallback; remove duplicate MCP bindings after workflow verification. | All three workflows pass without Tender MCP. |
| Custom Imported tools | Owning app's `BaseTool` or temporary compatibility path | Migrate one implementation at a time; no automatic conversion based only on import path. | Owner confirms behavior and permissions. |
| Custom Script tools | Reviewed version-controlled `BaseTool` or explicit exception | Do not silently execute database-stored code through FAC. | Every Script row has a reviewed disposition. |

### User stories

1. As a tool owner, I want one version-controlled `BaseTool` implementation and one FAC
   configuration record, so that runtime behavior and access policy have a single owner.
2. As an agent administrator, I want to select FAC tools per agent, so that agent
   capabilities are explicit and do not depend on legacy `AI Tool` rows.
3. As an end user, I want migrated tools to preserve my Frappe permissions and confirmation
   prompts, so that migration does not grant additional access or remove safety checks.
4. As a migration operator, I want a dry-run report showing exact, unmatched, and ambiguous
   tools, so that uncertain mappings are reviewed instead of migrated silently.
5. As a reviewer, I want behavior, budget, audit, and workflow evidence for each tool group,
   so that deletion of the legacy system is based on proof rather than configuration alone.
6. As an operator, I want a reversible cutover before deletion, so that a failed migration
   can be rolled back without reconstructing tool definitions from memory.

### Migration phases

#### Phase 0 — Freeze, inventory, and backup

- [ ] Announce that new tools must not be added only to `AI Tool`; new work must target
  `BaseTool` plus FAC configuration.
- [ ] Export all `AI Tool` rows, including `slug`, type, import path, Script code,
  description, enabled state, and confirmation policy.
- [ ] Export every legacy `AI Agent Tool` binding and every `AI Agent Plugin Tool` binding
  across each installed site.
- [ ] Inventory code and data references to `AI Tool`, `AI Agent Tool`, `AI Agent.tools`,
  `AI Agent Tool Config`, `AI FAC Tool`, and `AI MCP Tool.matched_ai_tool`.
- [ ] Inventory all registered Assistant Core tools and all `FAC Tool Configuration` rows,
  including `module_path`, source app, enabled state, category, and role access.
- [ ] Capture a database backup and store the migration report with the deployment
  artifacts before changing agent bindings.
- [ ] Record the current successful behavior of each production agent, especially the three
  tender workflows.

**Exit criteria:** the inventory is complete, every legacy row has an owner, and the
rollback backup/report exists.

#### Phase 1 — Establish the canonical FAC implementations

- [ ] For each exact Assistant Core equivalent, record the mapping without duplicating an
  implementation in `frappe_ai`.
- [ ] For `frappe_ai`-owned tools, ensure the `BaseTool` classes are registered through
  `frappe_ai/hooks.py` and discoverable by the Assistant Core registry.
- [ ] For tender tools, ensure the `BaseTool` classes are registered through
  `tender_automation/hooks.py`; do not create one plugin implementation per agent.
- [ ] For every wrapper, preserve the existing business logic rather than creating a
  second implementation.
- [ ] Ensure FAC synchronization creates or updates `FAC Tool Configuration` rows with the
  correct `tool_name`, `source_app`, `module_path`, description, category, and enabled state.
- [ ] Resolve name collisions before binding agents. Only exact stable tool-name matches
  may be automated.

**Exit criteria:** every intended replacement is discoverable from the Assistant Core
registry and has one canonical implementation path.

#### Phase 2 — Migrate behavior and security semantics

- [ ] Compare public input schemas and output shapes between each legacy tool and its FAC
  replacement.
- [ ] Verify `get_available_tools(user=...)` hides disabled and role-restricted tools.
- [ ] Verify `dispatch_plugin_tool()` installs the acting user and restores the previous
  user in all success and failure paths.
- [ ] Verify Frappe DocType permissions, user permissions, ownership, and validation apply
  identically for read and mutation operations.
- [ ] Map `requires_confirmation` from each legacy agent binding to the plugin binding;
  verify both approve and deny behavior.
- [ ] Verify direct FAC calls consume `AI Run` budgets. Record remote MCP budget coverage
  separately; it is not a reason to preserve `AI Tool`.
- [ ] Verify Assistant Core audit records and `AI Run` records both capture the migrated
  call and can be correlated.
- [ ] Complete and review the `execute` sandbox comparison before disabling its legacy
  implementation.

**Exit criteria:** the replacement is permission-equivalent, confirmation-equivalent, and
has explicit behavior evidence for every migrated tool.

#### Phase 3 — Dry-run and agent binding migration

- [ ] Run the existing migration/reporting path after FAC configuration synchronization.
- [ ] For each exact match, create an `AI Agent Plugin Tool` row pointing directly to the
  matching FAC configuration.
- [ ] Copy the legacy agent-level enabled and confirmation settings explicitly.
- [ ] Do not overwrite shared FAC global settings merely to mirror one legacy agent.
  Report conflicts for review.
- [ ] Refuse automatic migration for missing or ambiguous matches; add them to the report.
- [ ] Prevent duplicate plugin rows on reruns and verify idempotency.
- [ ] Leave the legacy `AI Agent Tool` rows and `AI Tool` records untouched during this
  phase so rollback remains a data-only operation.
- [ ] Review the migration report site by site and mark each row `Migrated`, `Manual`,
  `Retained`, or `Rejected` with an owner and reason.

**Exit criteria:** every production agent has an intentional FAC binding or a documented
exception, and rerunning the migration produces no duplicate bindings.

#### Phase 4 — End-to-end verification

- [ ] Test registry discovery for an allowed user, a disabled tool, and a role-restricted
  tool.
- [ ] Test read-only tools with permitted and forbidden DocTypes.
- [ ] Test create/update/delete with confirmation, permission failures, validation errors,
  partial failures, and budget limits.
- [ ] Test `execute` against the forbidden namespace (`frappe.db.sql`, `frappe.qb`,
  `frappe.db.set_value`, `frappe.get_all`) and permitted operations.
- [ ] Test knowledge search cannot widen beyond the agent's persisted knowledge bases.
- [ ] Test memory writes cannot choose another agent, user scope, or source run.
- [ ] Test tool failures are normalized into the model/run path and do not leave runs stuck.
- [ ] Test Assistant Core and `frappe_ai` audit records.
- [ ] Run Spec Review, Historical Match, and SAP Match through direct FAC bindings with
  known-good data and compare results to the recorded baseline.
- [ ] Test that a service restart, callback failure, and stream termination leave runs in
  a recoverable terminal state.

**Exit criteria:** all acceptance evidence is attached to the migration report; no workflow
depends on the legacy dispatch path for normal operation.

#### Phase 5 — Runtime cutover and compatibility quarantine

- [ ] Make `AI Agent Plugin Tool` / FAC the only path used for newly configured agents.
- [ ] Keep legacy rows readable and recoverable, but stop using them to build new runtime
  configuration.
- [ ] Update frontend tool metadata APIs to read FAC registry metadata instead of loading
  `AI Tool` rows.
- [ ] Update migration/setup code so future installs do not seed new `AI Tool` rows for
  migrated capabilities.
- [ ] Remove normal runtime dependencies on `dispatch_tool()` and legacy resolver calls.
- [ ] Keep the migration command/report available until the quarantine period ends.
- [ ] Run the full app and focused integration suites after the cutover.

**Exit criteria:** production agents run through FAC, legacy rows are not authoritative,
and the application no longer creates new legacy tool state.

#### Phase 6 — Delete the redundant legacy system

Deletion is a separate change and must not be bundled with the first migration attempt.

- [ ] Confirm zero active `AI Agent Tool` bindings and zero unclassified `AI Tool` rows.
- [ ] Confirm no runtime, frontend, trigger, migration, test, or setup code requires
  `AI Tool` except an explicitly retained archival/report reader.
- [ ] Confirm all required tool implementations are registered `BaseTool` classes and all
  FAC configurations have valid module paths.
- [ ] Confirm no production workflow depends on the Tender MCP fallback.
- [ ] Archive the final mapping and verification report outside the database.
- [ ] Remove the deprecated `AI Agent.tools` field and its compatibility child table only
  after the previous gates pass.
- [ ] Remove `AI Tool` and its controller/resolver code only after the previous gates pass.
- [ ] Remove `AI Agent Tool Config` if no compatibility reader remains.
- [ ] Audit `AI FAC Tool` separately. It is a local mirror, not the Assistant Core source
  of truth; remove it if no code or UI requires it after FAC metadata is consumed directly.
- [ ] Remove obsolete migration branches only after one release/deployment cycle with no
  rollback request.
- [ ] Update specifications, README, setup guides, and migration docs to describe FAC as
  canonical and mark the legacy system removed.

**Exit criteria:** the old DocTypes and dispatch path can be removed without losing an
implementation, agent binding, permission rule, audit record, or rollback artifact.

### Data migration rules

1. Match `AI Tool.slug` to `FAC Tool Configuration.tool_name`; do not match by title or
   description.
2. Require exactly one FAC match. Missing and ambiguous matches are manual review items.
3. Treat `FAC Tool Configuration` as global policy and `AI Agent Plugin Tool` as agent
   selection. Do not copy an agent-specific flag into global FAC configuration.
4. Copy legacy confirmation policy to each plugin binding explicitly.
5. Do not assume an `AI Tool.import_path` can be placed into FAC `module_path`; the target
   path must identify a registered `BaseTool` class.
6. Do not migrate Script code automatically. A database code blob must receive a reviewed
   implementation disposition.
7. Make the migration idempotent. Re-running it must not duplicate agent bindings or
   change reviewed exceptions.
8. Keep legacy data until the deletion phase. Migration and deletion are separate commits
   or deployable changes.

### Verification strategy

Use the highest public seam available for each claim:

- Assistant Core registry discovery for tool availability, metadata, and role filtering.
- `dispatch_plugin_tool()` for acting-user, permission, budget, and error behavior.
- `start_run` → FastAPI SSE → Frappe callback for full agent-run behavior.
- Direct workflow entry points for tender baseline/result comparisons.
- Migration reporting for data completeness and idempotency.

Avoid declaring parity from unit tests that only inspect schemas or mocked registry calls.
The deletion gate requires at least one integration-level execution for every migrated
tool family and real workflow verification for every production agent.

### Rollback plan

Rollback remains possible until the final deletion phase:

- Restore the previous agent bindings from the migration report or database backup.
- Re-enable the legacy `AI Tool`/`AI Agent Tool` rows without rewriting their definitions.
- Repoint the affected agent to the legacy path only for the affected tool family.
- Keep FAC rows and BaseTool implementations in place; they are additive and safe to leave
  during rollback.
- If deletion has already shipped, restore the archived DocType definitions and data from
  the pre-deletion backup before restarting workers and running migrations.

No destructive deletion should occur until the final mapping report, database backup,
and rollback rehearsal are complete.

### Definition of done

- [ ] One canonical `BaseTool` implementation exists for every migrated capability.
- [ ] One FAC configuration exists for every canonical tool and its module path resolves.
- [ ] Every production agent uses `AI Agent Plugin Tool` for migrated tools.
- [ ] No new agent setup creates legacy `AI Tool` bindings.
- [ ] Permissions, confirmation, budgets, schemas, outputs, and audits are verified.
- [ ] Tender workflows pass through direct FAC without MCP fallback.
- [ ] `execute` has a reviewed sandbox decision.
- [ ] `run_action` has an explicit replacement and parity evidence.
- [ ] Every unmatched or retained legacy row has a documented owner and reason.
- [ ] The final report and rollback artifact are archived.
- [ ] Legacy DocTypes, fields, and runtime code are removed only after all gates pass.

### Review log

Use this table during review and implementation. Add a dated row whenever a phase or
decision is accepted; do not mark a phase complete based only on code presence.

| Date | Reviewer | Phase/decision | Status | Evidence or follow-up |
|---|---|---|---|---|
| 2026-08-31 |  | Target architecture and no-alias decision | 🟡 Pending review |  |
|  |  | Tool inventory and classification | ⬜ Not started |  |
|  |  | FAC implementation parity | ⬜ Not started |  |
|  |  | Agent data migration | ⬜ Not started |  |
|  |  | End-to-end verification | ⬜ Not started |  |
|  |  | Runtime cutover | ⬜ Not started |  |
|  |  | Legacy deletion | ⬜ Not started |  |

---

## Completed

- **2026-08-19** — Audited the previously-written `DOCTYPE_CLEANUP_PLAN.md` and
  spec 007 against live code and live data on `tact.local`. Found their premise
  false: `ai_tool`/`ai_agent_tool`/`ai_mcp_tool` are load-bearing, not dead code.
  `ai_mcp_server_profile` and `ai_mcp_server_tool` were confirmed genuinely dead
  (orphaned `__pycache__`-only directories, untracked, absent from git history) and
  deleted.
- **2026-08-19** — Verified a real Assistant Core MCP connection against
  `tact.local`: raw `initialize` and `tools/list` JSON-RPC calls over
  `POST /api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp` with
  `Authorization: token <api_key>:<api_secret>` succeeded. Server identifies as
  `frappe-assistant-core 2.0.0`, protocol `2025-06-18`. 24 live tools discovered.
  Test credentials for a user with `assistant_enabled=1` stored in
  `sites/tact.local/site_config.json` as `frappe_assistant_core_test_api_key` /
  `frappe_assistant_core_test_api_secret` (dev-only; not committed — site_config.json
  is per-site local state, not versioned).
- **2026-08-19** — Built the real tool mapping table (now in spec 007 Part 4) from live data:
  6 of 10 `frappe_ai` builtins have clean Assistant Core equivalents, `execute` needs
  a sandbox-behavior comparison before it can be trusted, and `run_action` /
  `search_knowledge` / `update_memory` plus all 7 `tender_automation` custom tools
  have no Assistant Core equivalent yet.
- **2026-08-19** — Confirmed the `assistant_tools` hook (discovered by
  `frappe_assistant_core/plugins/custom_tools/plugin.py`) is the correct mechanism
  for `frappe_ai` and `tender_automation` to each contribute their own tools to
  Assistant Core, keeping tool code owned by the app it belongs to. No app currently
  uses this hook — confirmed via repo-wide grep — so this is new integration work,
  not a pattern to copy from an existing example.
- **2026-08-19** — Wrote spec 010 laying out Phases 1-7, then merged it into
  [spec 007](../specifications/007-mcp-integration-and-cleanup.md) so there is one
  authoritative document instead of two. Rewrote `DOCTYPE_CLEANUP_PLAN.md` to
  retract its incorrect removal recommendation for
  `ai_tool`/`ai_agent_tool`/`ai_mcp_tool`.

## Completed (continued)

- **2026-08-19** — Created a real `AI MCP Connection` ("Assistant Core",
  streamable-http) on `tact.local` and ran `check_connection()` against it. Found and
  fixed a real bug in `frappe_ai/api/mcp.py`'s `_build_toolkit()`:
  `MCPTools(headers=...)` is not a valid Agno API (verified against the installed
  Agno version — `Toolkit.__init__() got an unexpected keyword argument 'headers'`).
  Correct form is `MCPTools(transport="streamable-http",
  server_params=StreamableHTTPClientParams(url=..., headers=...))`. The same fix was
  subsequently applied to `frappe_ai/service/builder.py` and covered by the builder
  test.
  Updated spec 007's code snippets and "already done" claims to reflect this — that
  document's Phase 1/2 claims were unverified until this pass, not accurate as
  originally stated.
- **2026-08-19** — Design decision: Phase 4/5's tool-binding mechanism generalized
  beyond MCP-only. Confirmed (via code investigation, not assumption) that Assistant
  Core's `ToolRegistry` (`tool_registry.py:264` `execute_tool`, `:218`
  `get_available_tools`) is callable fully in-process — no MCP transport, no HTTP,
  no `AI MCP Connection` record — and is the mechanism `fac_endpoint.handle_mcp`
  itself uses internally. Also confirmed no `@plugin`/`@tool`/`@mcp` decorator
  exists anywhere in Assistant Core (100% of its 24 tools use `BaseTool`
  subclassing; `mcp/server.py` docstring explicitly states the decorator pattern
  "is not supported"). Per user guidance: `AI MCP Connection` is for remote MCP
  servers (or local ones by deliberate choice, e.g. `tender-mcp`); direct plugin
  linkage is **preferred** for local Assistant Core tools. Designed as a new,
  separate schema — `AI Agent Plugin Tool` child table + `AI Agent.plugin_tools`
  field, parallel to (not folded into) `AI Agent MCP Connection` — plus
  `_resolve_agent_plugin_tools()`, `dispatch_plugin_tool()`, and a `dispatch`
  parameter on `builder.py`'s `_build_tool()`. Folded into spec 007 Part 4/5
  (Phase 4/5 sections) and the DocTypes Summary rather than a new spec number, to
  avoid re-fragmenting the plan the way merging 010 into 007 was meant to fix.
  The schema/code was implemented in commits `c0e99a3` and `acab633`; behavior and
  production-agent migration verification remain incomplete.

## In Progress

- Direct FAC runtime path is implemented: `AI Agent Plugin Tool` links to
  `FAC Tool Configuration`, `ToolRegistry` supplies availability/schema metadata,
  and `dispatch_plugin_tool` executes under the acting user.
- Context-sensitive FAC tools are now server-scoped: `search_knowledge` receives
  knowledge bases from the persisted run's agent, and `update_memory` receives the
  persisted agent/source-run identity rather than model-supplied values.
- Guest callback loading is hardened by explicitly owner-checking the run and loading
  the run graph from the database before building service config.
- Budgets are counted by the Frappe dispatch endpoints, but the independent remote
  MCP transport still bypasses those counters.

## Priority 1 — Tender migration

The tender tools are local application code. They will be registered once as
Assistant Core `BaseTool` implementations and selected per agent through
`AI Agent Plugin Tool` bindings. No plugin implementation is created per agent.

The migration target is all ten local tender capabilities:

- Seven Frappe-side context, persistence, and failure tools.
- Three tools currently exposed by the local Tender MCP server:
  `extract_tender_documents`, `match_historical_enquiries`, and
  `search_sap_sales_data`.

The existing Tender MCP connection remains available as a fallback during
migration. Direct FAC tools take precedence, and duplicate MCP names are filtered
from a run when the direct FAC tool is available. MCP is removed only after all
three tender workflows pass through direct FAC execution.

## Updated (2026-08-19)

- **2026-08-19** — Fixed `MCPTools(headers=...)` bug in `frappe_ai/service/builder.py`. 
  Changed from invalid `MCPTools(url=..., headers=...)` to correct 
  `MCPTools(transport="streamable-http", server_params=StreamableHTTPClientParams(url=..., headers=...))`.
  This matches the fix already applied to `frappe_ai/api/mcp.py`. Syntax verified.
- **2026-08-19** — Also fixed `frappe_ai/api/dispatch.py` to properly restore the 
  original user in a `finally` block after `frappe.set_user(user)`.
- **2026-08-19** — Fixed `frappe_ai/api/frontend.py` to include tool summaries 
  in MCP connection display (previously returned empty `tool_summaries: []`).
- **2026-08-19** — Added 3 new tests in `frappe_ai/tests/test_mcp.py` for connection 
  creation and tool discovery.
- **2026-08-19** — Phase 2 testing: Verified Assistant Core MCP tools (create_document, 
  update_document, delete_document) work via direct JSON-RPC calls. 
  **Key Finding**: MCP-routed mutations do NOT enforce ADR 0008 budgets (max_mutations, 
  max_records_per_call) - this is a gap that needs to be addressed. Assistant Core's 
  tools run directly without going through frappe_ai's dispatch.py where budgets are enforced.

## Remaining Work

See [spec 007](../specifications/007-mcp-integration-and-cleanup.md) Parts 4-5
(Phases 1-7) in full. Headline items:

- Add permission, confirmation, budget, schema, and output-parity tests for the
  direct FAC path and all mutating tools.
- Decide how remote MCP calls receive the same ADR 0008 budgets; direct FAC dispatch
  counts usage, but an Agno MCP call currently does not.
- Complete the written `execute` versus Assistant Core `run_python_code` sandbox
  comparison; keep hardened `safe_exec` until approved.
- Add behavior-equivalence tests for the now agent-scoped `search_knowledge` and
  `update_memory` wrappers.
- Run known-good Spec Review, Historical Match, and SAP workflows through direct FAC
  bindings while retaining MCP fallback; remove the Tender MCP configuration only
  after those workflows pass.
- Resolve `run_action`, migrate/audit every production `AI Tool` row, and record
  unmatched or ambiguous rows.
- Remove legacy DocTypes from the active runtime authority while retaining
  `AI Tool` and `AI Agent Tool` as compatibility/migration records.

## Verification performed

- `frappe_ai.tests.test_api`: **34/34 passing** on 2026-08-21 with host-level database access, including the two direct-FAC scope tests.
- `frappe_ai.tests.test_builder`: 2/2 passing.
- Local E2E reached `start_run → HMAC token → FastAPI SSE → Frappe run-config
  callback → model execution`; the configured model returned its expected fake-key
  401, so successful LLM-generated tool selection remains unverified.
- Direct HTTP `dispatch_plugin_tool(get_doctype_info, Administrator)` returned a
  successful Assistant Core result.
- All ten tender tools are registered in Assistant Core on `tact.local`.
- All three tender agents have the expected direct FAC bindings; the existing
  Tender MCP bindings remain intact as fallback.
- Direct run configuration exposes FAC tools and filters duplicate MCP names.
- Tender migration was rerun successfully with no duplicate agent bindings.
- Tender FAC wrapper and orchestration tests pass: 12 tests total.
- The full tender suite currently has one unrelated failure in existing SAP match-row
  selection behavior: `test_only_last_sap_sales_data_match_row_remains_selected`.
- Full `bench migrate` could not run because Redis Queue was unavailable; the changed
  `AI Agent Plugin Tool` DocType was reloaded directly and verified to link to
  `FAC Tool Configuration`.
- Host-level service verification confirmed MariaDB, Frappe web, FastAPI, workers, and
  scheduler are running. The earlier MariaDB socket failures came from the restricted
  sandbox environment, which cannot access the host database network/socket.
- Disposable E2E agent/session/run records were removed after testing.

## Remaining blockers

- A real model credential is required to prove streamed success, FAC tool-call
  selection, and confirmation pause/resume through the LLM.
- Remote MCP execution still needs a budget-enforcement design and implementation.
- The three tender workflows still need known-good end-to-end execution through
  direct FAC tools before the Tender MCP connection can be removed.

## Change Log

| Date | Change |
|---|---|
| 2026-08-19 | Progress file created. Audit + verification + spec 010 written, then merged into spec 007. Dead orphaned doctype dirs removed. |
| 2026-08-19 | Designed direct Assistant Core plugin linkage (`AI Agent Plugin Tool`) as a preferred alternative to MCP for local tools; folded into spec 007 Phase 4/5 and DocTypes Summary. Design only — no schema/code implemented yet. |
| 2026-08-19 | Fixed builder.py MCPTools bug. Verified on live bench: check_connection returns is_connected=true with 24 tools, start_run returns valid stream_url. Phase 1 complete. |
| 2026-08-19 | Phase 2 complete: Tested create/update/delete via MCP. Found budget enforcement gap - ADR 0008 budgets do not apply to MCP-routed mutations. |
| 2026-08-19 | Phase 7 started: Removed `tools` field from ai_agent.json, removed tools type annotation and methods from ai_agent.py, simplified _resolve_agent_tools() in service.py to return empty list. |
| 2026-08-19 | Phase 4 started: Created `AI Agent Plugin Tool` child doctype, added `plugin_tools` field to AI Agent, implemented auto-population of MCP tools and plugin tools when linked to agent. |
| 2026-08-20 | Implemented direct FAC resolution/dispatch, migration reporting, run budgets, frappe_ai/tender Assistant Core registrations, and guest callback run loading. |
| 2026-08-20 | Verified API/builder tests and live HTTP boundaries. Direct FAC metadata dispatch passed; full model success was blocked by the configured fake provider key. |
| 2026-08-21 | Re-audited status: legacy DocTypes are intentionally retained for compatibility; active runtime authority remains the FAC path. |
| 2026-08-21 | Tender migration made Priority 1: all ten local tender capabilities will be registered once as FAC tools, with MCP retained only as a migration fallback. |
| 2026-08-21 | Registered all ten tender FAC tools, migrated all three tender agents to direct FAC bindings on `tact.local`, retained MCP fallback, and verified idempotent rerun. |
| 2026-08-21 | Focused migration/orchestration tests passed; full tender suite recorded one unrelated SAP selection failure, and full bench migrate remains pending Redis availability. |
| 2026-08-21 | Closed the direct-FAC scope gap: knowledge search is constrained to the run's agent knowledge bases, memory writes are constrained to the run's agent and source run, and two integration tests cover model-supplied scope override attempts. |
