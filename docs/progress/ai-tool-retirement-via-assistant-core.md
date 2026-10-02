# Progress — moving tools from `AI Tool` to Assistant Core

Tracks the retirement of the legacy tool records (`AI Tool`, `AI Agent Tool`) in favour of Assistant Core (FAC) tools. Background and the data flow are in [007](../specifications/007-mcp-integration-and-cleanup.md) and the [FAC integration guide](../FRAPPE_ASSISTANT_CORE_INTEGRATION.md); which DocTypes stay is in the [cleanup plan](../DOCTYPE_CLEANUP_PLAN.md).

## Status

**In progress. The tender migration is the priority.** Runtime already uses direct FAC bindings; the legacy records are kept as compatibility data until the gates below pass.

## The target

One tool path, and no alias between old and new:

```
BaseTool class in the owning app
   → registered in the FAC registry (hooks.py: assistant_tools)
   → AI Agent Plugin Tool row (which agent uses it, and whether it needs approval)
   → dispatch_plugin_tool  (api/dispatch.py)
   → the agent builder in the service
```

`AI Tool` must not become a permanent second source of truth (duplicate names, descriptions, flags and implementations would drift). Legacy rows are matched to FAC tools by stable name only to fill in agent bindings.

## Phase status

| Phase | Work | Status |
|---|---|---|
| 1 | Read-only built-in tools via FAC | Implemented; direct dispatch verified |
| 2 | Changing (mutating) tools via FAC | Direct path exists; budgets and approvals now enforced in Frappe. **MCP-routed calls remain uncounted** |
| 3 | `execute` sandbox parity | Wrapper exists; comparison and tests not finished |
| 4 | `frappe_ai`'s own tools as FAC contributions | Registered (`execute`, `run_action`, `search_knowledge`, `update_memory`); behaviour tests incomplete |
| 5 | Tender tools as contributions from the tender app | Direct migration done on the test site; workflow verification pending |
| 6 | `run_action` and a full parity audit | Not done |
| 7 | Retire the legacy runtime | The runtime no longer sends legacy tools to the service. The DocTypes are kept; some references remain |

## Done

- Direct bindings (`AI Agent Plugin Tool`), `_resolve_agent_plugin_tools`, `dispatch_plugin_tool`.
- Run-scoped arguments for `search_knowledge`, `update_memory`, `load_full_document_text` (the model cannot choose the agent, knowledge bases or model).
- An idempotent migration and report (`api/migration.py`, after `migrate`): exact name matches become plugin bindings; missing or ambiguous matches go to the report and are not migrated.
- All three tender agents repointed to direct FAC tools, with the MCP connection kept as a fallback; duplicate MCP tools are hidden when a direct tool exists.
- Approval and per-run limits apply to direct FAC calls.

## Open

1. **Tender workflows** (Spec Review, Historical Match, SAP Match) must each pass through direct FAC with a real model before the tender MCP connection is removed. Blocked on a valid model credential in the test environment.
2. **`execute`:** compare the FAC wrapper with the original sandbox, then decide.
3. **`run_action`:** choose its replacement and show parity.
4. **Remote MCP** calls need approval and budget accounting like direct calls.
5. **Retirement gates:** zero active `AI Agent Tool` bindings and no unclassified `AI Tool` rows; no runtime, frontend, trigger, test or setup code requiring the legacy records; every tool is a registered `BaseTool`; nothing depends on the tender MCP fallback; the mapping report is archived outside the database.

## Checklist for the cutover

1. **Freeze and inventory:** stop adding tools only to `AI Tool`; export all `AI Tool` rows, agent bindings and FAC configuration; take a backup; record each production agent's current behaviour.
2. **Canonical implementations:** every capability has one `BaseTool` class registered in FAC; resolve name collisions (only exact stable-name matches migrate).
3. **Behaviour and security:** compare schemas and outputs; check registry discovery hides disabled and role-restricted tools; check the acting user is installed and restored; check permissions and approvals; check budgets and audit records.
4. **Dry run:** run the migration report; create plugin rows for exact matches, copying the agent-level enabled and approval settings; do not overwrite shared FAC settings; make reruns idempotent; leave legacy rows untouched; review the report site by site.
5. **End-to-end tests:** permitted and forbidden reads; create, update and delete with approval, permission failures and validation errors; `execute` cannot reach `frappe.db.sql` or `frappe.qb`; knowledge search stays within the agent's knowledge bases; memory writes cannot choose another agent or scope; failures never leave runs stuck.
6. **Cutover:** FAC is the only path for new agents; legacy rows stay readable but are not used to build runtime; the frontend reads FAC metadata; setup stops seeding `AI Tool` rows.
7. **Delete** the legacy system (the `AI Agent.tools` field, `AI Tool`, `AI Agent Tool Config` and the resolver code) only after the gates pass and one release cycle without use.

**Rollback:** keep the legacy rows and the migration report until the quarantine period ends, so an agent can be rebound by hand.
