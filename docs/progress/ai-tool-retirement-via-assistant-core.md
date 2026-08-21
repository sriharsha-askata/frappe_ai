# Progress — Retire `AI Tool` / `AI Agent Tool` via Assistant Core

> Tracks [007 — MCP Integration & DocType Cleanup](../specifications/007-mcp-integration-and-cleanup.md) Parts 4-5 (the verified tool mapping and 7-phase retirement plan).
> Keep this file current as work happens.

---

## Overall Status

| | |
|---|---|
| **Status** | 🟡 Tender migration is Priority 1 |
| **Current phase** | Migrate tender tools to direct FAC bindings |
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
- Make `search_knowledge` and `update_memory` agent-scoped rather than the current
  generic wrappers, and add behavior-equivalence tests.
- Register all ten local tender capabilities as FAC tools; repoint the three tender
  agents through direct FAC bindings while retaining MCP fallback; then run known-good
  workflows before removing the Tender MCP configuration.
- Resolve `run_action`, migrate/audit every production `AI Tool` row, and record
  unmatched or ambiguous rows.
- Remove legacy DocTypes from the active runtime authority while retaining
  `AI Tool` and `AI Agent Tool` as compatibility/migration records.

## Verification performed

- `frappe_ai.tests.test_api`: 32/32 passing.
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
