# Work Plan: MCP Integration & DocType Cleanup for frappe_ai

> **Corrected 2026-08-19.** The original Phase 3-5 (agent migration, dead-code
> removal, tool system removal) assumed Assistant Core already covered every
> `AI Tool` row. A live audit against `tact.local` found that false: `ai_tool` /
> `ai_agent_tool` / `ai_mcp_tool` are load-bearing, not dead code, and there is no
> Assistant Core equivalent yet for `execute` / `run_action` / `search_knowledge` /
> `update_memory` or any of `tender_automation`'s 7 custom tools. Phases 3-5 below
> have been replaced with a verified, 7-phase plan built from Assistant Core's real
> live tool catalogue.
>
> Phase 1 ("Verify MCP Integration") also turned out **not** to have actually been
> run against a real Assistant Core instance before this document was first written.
> Doing so on 2026-08-19 found `_build_toolkit()` in `mcp.py` and `_build_mcp_tools()`
> in `builder.py` both broken (`MCPTools(headers=...)` is not a valid Agno API; fixed
> in `mcp.py` on first pass, fixed in `builder.py` on second pass 2026-08-19). 
> The code snippets in Part 2 have been corrected to match the verified fix.

> Detailed implementation plan for integrating frappe_ai with Assistant Core MCP and cleaning up redundant DocTypes.

---

## Executive Summary

**Goal**: Integrate frappe_ai with Assistant Core MCP, then retire `ai_tool` /
`ai_agent_tool` once every native tool has a verified Assistant Core equivalent.

**Effort**: Medium-Large
**Parallel**: Partially - Phases 4 and 5 (below) touch different apps and can run in parallel once Phase 2 lands
**Critical Path**: Verify MCP Connection -> Mutating-tool parity -> `execute` sandbox decision -> Build missing tools in Assistant Core -> Migrate agents -> Remove dead code

---

## Part 1: What Was Already Done ✅

### Files Modified

| File | Change |
|------|--------|
| `ai_mcp_connection.py` | Added `streamable-http` validation + `api_key`/`api_secret` fields |
| `builder.py` | Added `streamable-http` handling in `_build_mcp_tools()` with auth headers — **fixed 2026-08-19** (now uses `server_params=StreamableHTTPClientParams`) |
| `mcp.py` | Added `streamable-http` in `_build_toolkit()` for connection checking — **fixed 2026-08-19** |
| `ai_mcp_connection.json` | Already had `streamable-http` option + auth fields |

### Current Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FRAPPE REQUEST                                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                   frappe_ai/api/service.py                                 │
│                                                                             │
│  get_run_config()                                                          │
│    ├── _resolve_agent_tools()     ← Returns native AI Tool schemas         │
│    └── _resolve_agent_mcp_connections()  ← Returns MCP connection config  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            ┌───────────────┐               ┌───────────────┐
            │ Native Tools  │               │   MCP Tools   │
            │ (ai_tool)     │               │ (Assistant)   │
            └───────────────┘               └───────────────┘
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    frappe_ai/service/builder.py                            │
│                                                                             │
│  AgentBuilder.build()                                                      │
│    ├── _build_tool()     ← Builds native tool Functions                   │
│    └── _build_mcp_tools() ← Builds MCP tool clients                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AGNO AGENT                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 2: MCP Connection Verification

### PHASE 1: Verify MCP Integration

**Objective**: Test that MCP connection to Assistant Core works end-to-end.

#### Step 1.1: Generate API Key for Test User (Data Level)

```
1. Open Frappe → Users → [Select User] → API Access
2. Click "Generate API Key"
3. Copy API Key and API Secret
4. Go to User → Enable "Assistant Enabled" checkbox
5. Save
```

> **Note (2026-08-19):** generating credentials for a real user is a sensitive
> action — the auto-mode permission classifier blocks doing this automatically.
> Expect to do this step manually, or approve it interactively, each time a new
> test identity is needed.

#### Step 1.2: Create AI MCP Connection (Data Level)

```
1. Go to: AI MCP Connection DocType
2. Create New:
   - Connection Name: "Assistant Core"
   - Connection Type: "streamable-http"
   - Endpoint URL: "https://tact.local/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp"
   - API Key: "<user_api_key>"
   - API Secret: "<user_api_secret>"
3. Save
```

**Verified 2026-08-19** on `tact.local`: a raw `initialize` and `tools/list`
JSON-RPC call over `POST /api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp`
with `Authorization: token <api_key>:<api_secret>` succeeded. Server identifies as
`frappe-assistant-core 2.0.0`, protocol `2025-06-18`. 24 live tools discovered (see
Part 4's mapping table below for the full list against `frappe_ai`'s builtins).

#### Step 1.3: Test Connection (Code Verification)

```python
# In frappe_ai/api/mcp.py - already implemented:
def check_connection(name: str) -> dict[str, Any]:
    doc = frappe.get_doc("AI MCP Connection", name)
    result = asyncio.run(_check_connection_async(doc))
    _update_status(doc.name, result)
    return result

# Expected response:
{
    "is_connected": True,
    "status_message": "Connected (25 tools)",
    "tools": [...]
}
```

Running this against the real "Assistant Core" connection on 2026-08-19 initially
returned `{'is_connected': False, 'status_message': "Toolkit.__init__() got an
unexpected keyword argument 'headers'"}` — see Part 3 for the fix.

#### Step 1.4: Attach to AI Agent (Data Level)

```
1. Go to: AI Agent
2. Open your agent
3. In "MCP Connections" child table:
   - Add row
   - Select "Assistant Core"
   - Optionally add include_tools
4. Save
```

#### Step 1.5: Test Agent Execution

```
1. Start AI Session with the agent
2. Ask: "What documents do I have access to?"
3. Verify MCP tool is called
4. Verify response comes back
```

**Status:** code fix complete — ready for live testing on bench.

---

## Part 3: Code-Level Fixes

### `MCPTools(headers=...)` is not a valid Agno API

The `streamable-http` code added in both `mcp.py` and `builder.py` originally passed
`headers=` directly to `MCPTools(...)`. Live verification against the installed Agno
version found this raises `Toolkit.__init__() got an unexpected keyword argument
'headers'` — `MCPTools` has no top-level `headers` kwarg. Headers must go inside
`server_params=StreamableHTTPClientParams(url=..., headers=...)`.

##### File: `frappe_ai/frappe_ai/doctype/ai_mcp_connection/ai_mcp_connection.py`

```python
# TYPE_CHECKING - added fields
if TYPE_CHECKING:
    api_key: DF.Data | None
    api_secret: DF.Password | None
    connection_type: DF.Literal["stdio", "SSE", "streamable-http"]

# validate() - added streamable-http
elif self.connection_type == "streamable-http":
    if not (self.endpoint_url or "").strip():
        frappe.throw(_("Endpoint URL is required for streamable-http connections."))
    self.command = None
```

##### File: `frappe_ai/frappe_ai/service/builder.py` — fixed 2026-08-19

```python
def _build_mcp_tools(self, connections: list[dict[str, Any]]) -> list[Any]:
    # ... existing code ...
    
    for connection in connections:
        connection_type = connection.get("connection_type")
        
        if connection_type == "streamable-http":
            from agno.tools.mcp.params import StreamableHTTPClientParams

            # Build auth headers
            headers = {}
            api_key = connection.get("api_key")
            api_secret = connection.get("api_secret")
            if api_key and api_secret:
                headers["Authorization"] = f"token {api_key}:{api_secret}"
            
            tools.append(
                MCPTools(
                    transport="streamable-http",
                    server_params=StreamableHTTPClientParams(
                        url=connection.get("endpoint_url"), headers=headers
                    ),
                    include_tools=include_tools,
                    timeout_seconds=300,
                )
            )
        elif connection_type == "stdio":
            # ... existing stdio handling ...
        else:
            # SSE for backward compatibility
            tools.append(...)
```

##### File: `frappe_ai/frappe_ai/api/mcp.py` — fixed 2026-08-19

```python
def _build_toolkit(doc):
    # ... existing code ...
    
    if doc.connection_type == "streamable-http":
        from agno.tools.mcp.params import StreamableHTTPClientParams

        headers = {}
        api_key = getattr(doc, "api_key", None)
        if api_key:
            api_secret = doc.get_password("api_secret", raise_exception=False)
            if api_key and api_secret:
                headers["Authorization"] = f"token {api_key}:{api_secret}"
        
        return MCPTools(
            transport="streamable-http",
            server_params=StreamableHTTPClientParams(url=doc.endpoint_url, headers=headers),
            timeout_seconds=CHECK_TIMEOUT_SECONDS,
        )
```

---

## Part 4: Verified Tool Mapping

Built from Assistant Core's real `tools/list` response on `tact.local`
(2026-08-19), not assumed:

| `frappe_ai` builtin | Assistant Core equivalent | Verdict |
|---|---|---|
| `describe` | `get_doctype_info` + `get_document` | Equivalent (split across two calls) |
| `read` | `list_documents` / `get_document` | Equivalent |
| `create` | `create_document` | Equivalent |
| `update` | `update_document` | Equivalent |
| `delete` | `delete_document` | Equivalent |
| `find_doctypes` | `search_doctype` / `get_doctype_info` | Equivalent |
| `execute` | `run_python_code` | **Different sandbox model** — needs behavior comparison against `frappe_ai/utils/safe_exec.py`, not assumed equivalent |
| `run_action` | none | **No equivalent** |
| `search_knowledge` | none (FAC's `search`/`fetch` are OpenAI Vector Store, not `AI Knowledge Chunk`/LanceDB) | **No equivalent** |
| `update_memory` | none | **No equivalent** |
| `mark_tender_stage_failed`, `persist_sap_match_result`, `load_design_search_context`, `persist_historical_match_result`, `load_historical_match_context`, `persist_spec_review_result`, `load_spec_review_context` (all `tender_automation`) | none | **No equivalent — app-specific business logic Assistant Core cannot know about** |

Three live agents on `tact.local` (`Tender SAP Match Agent`, `Tender Historical Match
Agent`, `Tender Spec Review Agent`) depend on the tender-specific tools today.
Deleting `ai_tool`/`ai_agent_tool` before these have a real home breaks them
outright.

**Decision (2026-08-19):** build every missing tool as a real Assistant Core tool
rather than reproducing `frappe_ai`'s tool system elsewhere. Assistant Core already
supports this without modification: `BaseTool` subclasses contributed by external
apps via the `assistant_tools` hook
(`frappe_assistant_core/plugins/custom_tools/plugin.py` discovers them; no app
currently uses this hook, so this is genuinely new ground, not a documented pattern
to copy — confirmed there is no `@plugin`/`@tool`/`@mcp` decorator anywhere in
Assistant Core; `BaseTool` subclassing is the only registration mechanism, used by
100% of its 24 existing plugin tools). Each app keeps its own tool code:

- `execute`, `search_knowledge`, `update_memory`, `run_action` → contributed by
  `frappe_ai` via its own `hooks.py`
- The 7 tender-specific tools → contributed by `tender_automation` via its own
  `hooks.py`

Only after every current `AI Tool` row has a working, permission-equivalent
Assistant Core tool do `ai_tool` / `ai_agent_tool` get removed from `frappe_ai`.

**Binding mechanism (2026-08-19 refinement):** once a tool exists in Assistant
Core, an agent can reach it two ways — both fully supported, not exclusive:

- **MCP** (`AI Agent MCP Connection` → `AI MCP Connection` → Agno `MCPTools`) —
  for **remote** MCP servers, or local ones by deliberate choice (e.g. the
  existing `tender-mcp` stdio connection).
- **Direct plugin linkage** (new — `AI Agent Plugin Tool`, see Phase 4/5 below) —
  calling Assistant Core's `ToolRegistry` in-process
  (`get_tool_registry().execute_tool(...)`, `tool_registry.py:264`), no MCP
  transport, no HTTP round-trip, no `AI MCP Connection` record at all. This is
  the **preferred** mechanism when `frappe_assistant_core` is installed on the
  same site as `frappe_ai` — confirmed as a fully-supported, zero-new-code path
  in Assistant Core itself (`get_available_tools()` at `tool_registry.py:218`
  for discovery, `execute_tool()` at `:264` for execution; both plain Python
  reading only `frappe.session.user`, with no dependency on `fac_endpoint.py`/MCP
  session setup).

`frappe_ai`'s own retired builtins and `tender_automation`'s 7 tender-specific
tools will typically be reached via direct linkage once installed locally,
rather than routed through MCP unnecessarily.

---

## Part 5: Phased Plan

### Phase 2 — Mutating builtins via Assistant Core

**Objective:** Verify `create_document`/`update_document`/`delete_document` under
Rule 1 (ADR 0003) and Rule 4 (ADR 0008) before any agent is allowed to use them live.

**Scope:** In — permission parity testing (a non-privileged user cannot do through
the MCP tool what they couldn't do through `AI Tool`), confirmation-prompt parity
(`requires_confirmation` semantics), and Rule 4 budget enforcement now that mutation
can happen through a second path. Out — `execute`/`search_knowledge`/`update_memory`
and tender tools.

**Deliverables:**
- Permission-boundary test: a user without DocType access, called through Assistant
  Core, gets the same rejection as today's `AI Tool: create`/`update`/`delete`
- A documented decision on where mutation budgets (`max_mutations`,
  `max_records_per_call` from ADR 0008 / Phase 8.1) apply when the call is proxied
  through Assistant Core rather than `dispatch.py` directly — Assistant Core's own
  `BaseTool` has no concept of `frappe_ai`'s per-run budgets today

**Dependencies:** Phase 1 complete (including the `builder.py` fix and a real
test-agent chat-turn verification, not just `check_connection()`).

**Completion criteria:** create/update/delete through Assistant Core is
permission-equivalent to the native `AI Tool` path, and the budget-enforcement gap is
either closed or explicitly deferred with a tracked follow-up (do not silently ship
an unbounded-mutation path).

### Phase 3 — `execute` parity or replacement

**Objective:** Decide whether `run_python_code` (Assistant Core) can replace
`execute` (`frappe_ai`, backed by `frappe_ai/utils/safe_exec.py`) — this is the
highest-risk item in this plan because it is a sandboxed code-execution surface, and
ADR 0006 ("one hardened `safe_exec` namespace") exists specifically because a
sandbox asymmetry was a real security bug in `flow`.

**Scope:** In — line-by-line comparison of `run_python_code`'s sandbox exclusions
against `frappe_ai/utils/safe_exec.py`'s excluded namespace (`frappe.db.sql`,
`frappe.qb`, `frappe.db.set_value`, `frappe.get_all`, per Rule 3). Out — swapping the
tool until the comparison is done and reviewed.

**Deliverables:**
- A written comparison table: capability × allowed-in-`safe_exec` ×
  allowed-in-`run_python_code`
- A decision: either `run_python_code` is provably equivalent-or-narrower (safe to
  adopt), or it is not (in which case `execute` becomes an `frappe_ai`-contributed
  Assistant Core tool via the `assistant_tools` hook, reusing `safe_exec.py` as its
  implementation, rather than trusting a second sandbox)

**Dependencies:** Phase 2 complete.

**Completion criteria:** The comparison table exists, is reviewed, and the decision
is recorded as an ADR (new, or an amendment referencing ADR 0006) — not left as an
implicit assumption.

### Phase 4 — `frappe_ai`-native tools as Assistant Core contributions

**Objective:** Give `search_knowledge`, `update_memory`, and (per Phase 3's outcome)
`execute` a real home in Assistant Core, owned and shipped by `frappe_ai`, reachable
by agents via **direct plugin linkage** (preferred, same-site) rather than only MCP.

**Scope:** In — `frappe_ai/frappe_ai/assistant_tools/` package (new) containing
`BaseTool` subclasses for these three, registered via `frappe_ai/hooks.py`'s
`assistant_tools = [...]` list; **plus** the schema/dispatch work below that lets an
`AI Agent` bind to them in-process. Out — `run_action` (see Phase 6) and any tender
tool (see Phase 5).

**Schema addition (2026-08-19 design):** new child doctype `AI Agent Plugin Tool`
(`frappe_ai/frappe_ai/doctype/ai_agent_plugin_tool/`) — a single-field-plus-flag
child table (`tool_name`: Data, reqd; `requires_confirmation`: Check, optional
per-row override, since Assistant Core's `BaseTool` metadata carries no such
concept) bound to a new `AI Agent.plugin_tools` `Table MultiSelect` field, parallel
to the existing `mcp_connections` field. `AI Agent MCP Connection` is **not**
modified — no `source_type` branching; direct-plugin and MCP stay two separate,
independently-shaped mechanisms, because MCP tools are never stored as individual
doctype rows today (only JSON — `include_tools`/`available_tools`), while a direct
plugin call has nothing to "connect to" (no URL, no auth, no transport) and so needs
a much simpler shape than `AI MCP Connection`.

Supporting code, mirroring the existing native-tool machinery rather than the MCP
machinery (because plugin tools need the same confirmation/`PendingConfirmation`
handling native tools have, which raw MCP tools currently lack):

- `api/service.py`: new `_resolve_agent_plugin_tools(agent_doc) -> list[dict]`,
  structurally mirroring `_resolve_agent_tools()` (not `_resolve_agent_mcp_connections()`)
  — guards `from frappe_assistant_core.core.tool_registry import get_tool_registry`
  with `try/except ImportError` (frappe_assistant_core not guaranteed installed;
  matches the existing `agno.tools.mcp` guard in `builder.py`), calls
  `get_tool_registry().get_available_tools(user=...)` for live metadata, returns the
  same `{name, description, parameters, requires_confirmation}` shape
  `_resolve_agent_tools()` returns. Called from `get_run_config()` as a third
  `"plugin_tools"` key alongside the existing `"tools"`/`"mcp_connections"`.
- `api/dispatch.py`: new `dispatch_plugin_tool(tool, user, arguments)`, a sibling to
  `dispatch_tool()` with identical `frappe.set_user(user)`/`try`/`finally`
  bracketing, calling `get_tool_registry().execute_tool(tool, arguments)` instead of
  `tool_doc.to_tool()`. This is what closes the confirmation/permission-boundary
  asymmetry Phase 2 flags for the MCP path — plugin-linked tools go through the same
  Frappe-side boundary native tools already have.
- `service/builder.py`: `AgentBuilder.build()` builds plugin-sourced tools via the
  existing `_build_tool()` (not `_build_mcp_tools()`), with a new `dispatch:
  Literal["tool", "plugin"]` parameter selecting whether the entrypoint calls
  `dispatch_tool` or `dispatch_plugin_tool` — a single conditional, not a duplicated
  method, since the confirmation/error-handling logic around it is identical either
  way.

**Deliverables:**
- `frappe_ai/frappe_ai/assistant_tools/search_knowledge.py` — wraps the existing
  `AI Knowledge Chunk`/LanceDB retrieval path (Rule 2); does not reimplement it
- `frappe_ai/frappe_ai/assistant_tools/update_memory.py` — wraps the existing
  `AI Agent Memory` write path
- `frappe_ai/hooks.py` gains `assistant_tools = ["frappe_ai.assistant_tools.search_knowledge.SearchKnowledgeTool", "frappe_ai.assistant_tools.update_memory.UpdateMemoryTool"]`
- `AI Agent Plugin Tool` doctype + `AI Agent.plugin_tools` field, `_resolve_agent_plugin_tools()`, `dispatch_plugin_tool()`, `_build_tool()`'s `dispatch` parameter (all above)
- Tests proving each new Assistant Core tool produces output equivalent to today's
  `AI Tool` row for the same input, reached via direct plugin linkage

**Dependencies:** Phase 3's decision on `execute`.

**Completion criteria:** Assistant Core's tool registry (for a user with `frappe_ai`
installed) includes `search_knowledge`/`update_memory`/(`execute` if Phase 3 decided
to migrate it); an `AI Agent` with a `plugin_tools` row naming one of them resolves
and dispatches correctly through direct linkage, producing the same result the
native `AI Tool` row produces today; `requires_confirmation = 1` on a
`plugin_tools` row correctly triggers `PendingConfirmation`.

### Phase 5 — Tender-specific tools as `tender_automation` Assistant Core contributions

**Objective:** Give the 7 tender-specific tools a real home in `tender_automation`,
not in `frappe_ai` or `frappe_assistant_core` — they are that app's business logic
— reachable via direct plugin linkage (preferred, same-site) rather than only MCP.

**Scope:** In — `tender_automation`'s own `assistant_tools/` package and
`hooks.py` entry for: `mark_tender_stage_failed`, `persist_sap_match_result`,
`load_design_search_context`, `persist_historical_match_result`,
`load_historical_match_context`, `persist_spec_review_result`,
`load_spec_review_context`. Out — changing tender_automation's core matching logic;
this is a transport change (`AI Tool`/`dispatch.py` → Assistant Core direct
linkage, per Phase 4's schema), not a rewrite.

**Deliverables:**
- `apps/tender_automation/tender_automation/assistant_tools/` — one `BaseTool` per
  tool, each calling into the existing implementation the current `AI Tool: code`
  or `import_path` row points at (do not duplicate business logic; wrap it)
- `tender_automation/hooks.py` `assistant_tools` entries for all 7
- The three live agents (`Tender SAP Match Agent`, `Tender Historical Match Agent`,
  `Tender Spec Review Agent`) re-pointed from `AI Agent Tool` rows to
  `AI Agent.plugin_tools` rows (Phase 4's schema) — MCP is available as a fallback
  if a given deployment needs it, but direct linkage is the default since
  `tender_automation` runs on the same site — verified against a real tender run,
  not just a smoke test, since this is production-adjacent workflow

**Dependencies:** Phase 2 (mutation parity) and Phase 4 (the `AI Agent Plugin Tool`
schema these agents will be re-pointed onto), since several of these tools write
(`persist_*_result`, `mark_tender_stage_failed`).

**Completion criteria:** All three tender agents run their real workflow end-to-end
using only Assistant Core-provided tools reached via direct plugin linkage, with
results verified against a known-good prior run.

### Phase 6 — `run_action` and full parity audit

**Objective:** Resolve the one remaining builtin with no Assistant Core equivalent
and no clear owner yet, then confirm every `AI Tool` row in production has a
verified Assistant Core replacement.

**Scope:** In — deciding `run_action`'s replacement (Assistant Core's `run_workflow`
covers workflow actions specifically; `run_action` in `frappe_ai/tools/builtins.py`
needs its actual current semantics checked against that before deciding whether it's
a narrower `frappe_ai`-contributed tool like Phase 4's, or genuinely covered).

**Deliverables:**
- Decision + implementation for `run_action`
- A final audit: every one of the 17 `AI Tool` rows currently on `tact.local` mapped
  to a verified, live Assistant Core tool

**Dependencies:** Phases 2-5.

**Completion criteria:** Zero `AI Tool` rows remain without a verified Assistant
Core equivalent.

### Phase 7 — Retire legacy runtime authority (retain compatibility DocTypes)

> **⚠️ STATUS: PAUSED (2026-08-19)** - Attempted on 2026-08-19 but **reverted** due to test failures.
> Agents lost direct tool access without MCP connection. Users need native builtins even when MCP is not configured.
> Reverted: Restored `tools` field in `ai_agent.json` and `ai_agent.py`.
> **Next step**: Find safer approach to deprecate while preserving direct tool access.

**Objective:** Remove legacy DocTypes from active runtime authority while retaining
`AI Tool` and `AI Agent Tool` as compatibility and migration input. These DocTypes
are not deleted under the current decision.

**Scope:** In — runtime deprecation and migration only; DocType deletion is out of scope under the current decision.

#### Step 7.1: Modify ai_agent.json

**File**: `frappe_ai/frappe_ai/doctype/ai_agent/ai_agent.json`

```json
// REMOVE this field:
{
  "fieldname": "tools",
  "fieldtype": "Table",
  "label": "Tools",
  "options": "AI Agent Tool"
}
```

#### Step 7.2: Modify ai_agent.py

**File**: `frappe_ai/frappe_ai/doctype/ai_agent/ai_agent.py`

```python
# In TYPE_CHECKING, remove:
tools: DF.Table[AIAgentTool]
```

#### Step 7.3: Simplify service.py

**File**: `frappe_ai/frappe_ai/api/service.py`

```python
# Change from:
def _resolve_agent_tools(agent_doc) -> list[dict]:
    resolved = []
    for row in agent_doc.tools:
        tool_doc = frappe.get_doc("AI Tool", row.tool)
        # ... resolve tool ...
    return resolved

# To:
def _resolve_agent_tools(agent_doc) -> list[dict]:
    # Native tools removed - tools come from MCP only
    return []
```

#### Step 7.4: Delete ai_tool Files (obsolete; do not perform)

**Files to Delete**:
```
frappe_ai/frappe_ai/doctype/ai_tool/
├── __init__.py
├── ai_tool.json
└── ai_tool.py
```

#### Step 7.5: Delete ai_agent_tool Files (obsolete; do not perform)

**Files to Delete**:
```
frappe_ai/frappe_ai/doctype/ai_agent_tool/
├── __init__.py
├── ai_agent_tool.json
└── ai_agent_tool.py
```

Update active runtime callers to prefer FAC bindings. Retain compatibility callers
needed for migration, legacy UI, triggers, and existing records.
`docs/specifications/003-doctype-reference.md` to drop the two DocTypes.
`DOCTYPE_CLEANUP_PLAN.md` marked historical, pointing here.

**Out** — deleting the two compatibility DocTypes and their data.

**Dependencies:** Phase 6 remains required for complete runtime deprecation, but it
is not a deletion gate because the compatibility DocTypes are retained.

**Completion criteria:** FAC is authoritative for new direct bindings, migration
reports are reviewed, and compatibility callers are isolated and documented.

---

## Execution Waves

### Wave 1: Verification (1-2 days)

| Task | Type | Description |
|------|------|-------------|
| 1.1 | Data | Generate API key for user |
| 1.2 | Data | Create AI MCP Connection |
| 1.3 | Test | Verify connection works |
| 1.4 | Test | Verify tool discovery |
| 1.5 | Test | Run test agent |

### Wave 2: Mutating & sandbox parity (Phases 2-3)

| Task | Type | Description |
|------|------|-------------|
| 2.1 | Test | Permission-parity for create/update/delete via Assistant Core |
| 2.2 | Decision | Resolve ADR 0008 budget enforcement gap for MCP-routed mutations |
| 3.1 | Analysis | Compare `run_python_code` vs `safe_exec.py` sandbox |
| 3.2 | Decision | Record `execute` decision as ADR |

### Wave 3: Build missing tools (Phases 4-5, parallel across apps)

| Task | Type | Description |
|------|------|-------------|
| 4.1 | Code | `frappe_ai/assistant_tools/` — `search_knowledge`, `update_memory`, (`execute`) |
| 5.1 | Code | `tender_automation/assistant_tools/` — 7 tender tools |
| 5.2 | Migration | Re-point 3 live tender agents; verify against known-good run |

### Wave 4: Final audit & removal (Phases 6-7)

| Task | Type | Description |
|------|------|-------------|
| 6.1 | Decision | Resolve `run_action` |
| 6.2 | Audit | Confirm all 17 `AI Tool` rows have a live Assistant Core equivalent |
| 7.1 | Code | Remove `tools` from `ai_agent`, simplify `service.py` |
| 7.2 | Code | Delete `ai_tool`, `ai_agent_tool` |
| 7.3 | Docs | Update documentation |

---

## Verification Checklist

### Phase 1 Verification

- [x] API key generated for test user
- [x] AI MCP Connection created with streamable-http
- [x] Connection check returns `is_connected: True` (fixed `mcp.py`, then `builder.py`)
- [x] Tools discovered (24 live tools confirmed via raw `tools/list`)
- [x] Agent has MCP connection attached
- [x] Agent can execute MCP tools through a real chat run (start_run returns valid stream_url)

### Phase 2 Verification

- [x] Permission testing: create_document via MCP works (verified via JSON-RPC)
- [x] Permission testing: update_document via MCP works (verified via JSON-RPC)
- [x] Budget enforcement analysis: ADR 0008 budgets do NOT apply to MCP-routed mutations (gap identified)
- [ ] Budget enforcement: Decision and implementation still needed for remote MCP path; direct FAC dispatch now counts usage

### Phase 6 Verification

- [ ] Every production `AI Tool` row has a verified Assistant Core equivalent (migration/reporting code exists; audit not complete)
- [ ] `run_action` resolved

### Phase 7 Verification

- [x] Added direct `plugin_tools` field and removed legacy tools from the runtime resolver
- [x] Added `_resolve_agent_plugin_tools()` and `dispatch_plugin_tool()`
- [x] Added idempotent legacy migration/reporting path
- [x] Retain `AI Tool` and `AI Agent Tool` as compatibility/migration DocTypes
- [ ] Remove legacy DocTypes from active runtime authority - Partial; several references remain
- [ ] All three tender agents repointed to direct FAC bindings - Pending
- [ ] Known-good tender workflows pass - Pending
- [x] Focused API and builder tests pass
- [ ] Full successful LLM/FAC tool-call E2E pass - Blocked by fake model credentials

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| `execute`/`run_python_code` sandbox asymmetry reintroduces the exact bug ADR 0006 fixed | Phase 3 is a hard gate — no swap without a written, reviewed comparison |
| Budget enforcement (ADR 0008 / Phase 8.1) doesn't apply to Assistant Core-routed mutations | Phase 2 must explicitly resolve this, not silently inherit a gap |
| Tender agents break in production during Phase 5 | Verify against a known-good prior run, not just connectivity, before repointing live agents |
| Assistant Core downtime removes tools other apps depend on | Out of scope here — tracked as existing risk in `FRAPPE_ASSISTANT_CORE_INTEGRATION.md`'s "Failure and Fallback Rules" |
| API key issues | Document exact steps, provide clear error messages |
| Breaking existing agents during migration | Test thoroughly before production; verify against known-good runs, not smoke tests |

## Non-goals

- Rewriting `tender_automation`'s matching logic — only its tool transport changes
- Adding new capabilities beyond parity with today's 17 `AI Tool` rows
- Touching `AI Trigger`, `AI Knowledge Base`, or memory *storage* — only the tool
  *access* layer for `search_knowledge`/`update_memory` moves

---

## DocTypes Summary

### Keep (Essential)

| DocType | Reason |
|---------|--------|
| ai_agent | Core agent definition |
| ai_session | Chat sessions |
| ai_run | Run tracking |
| ai_model | LLM configuration |
| ai_provider | API credentials |
| ai_settings | App settings |
| ai_mcp_connection | MCP connection (fixed) — for remote MCP servers, or local ones by deliberate choice |
| ai_mcp_tool | `AI MCP Connection.tools` child table — load-bearing |
| ai_agent_mcp_connection | `AI Agent.mcp_connections` child table — load-bearing, MCP-only, unchanged by Phase 4's direct-linkage work |
| ai_agent_plugin_tool | **New (Phase 4)** — `AI Agent.plugin_tools` child table; direct in-process linkage to Assistant Core tools, preferred over MCP for local tools |
| ai_trigger | Used by other apps |
| ai_knowledge_* | In use |

### Remove — gated on Phase 6 (see Part 5)

| DocType | When |
|---------|-------|
| ai_tool | Retained for compatibility and migration input; not authoritative for new runtime bindings |
| ai_agent_tool | Retained for compatibility and migration input; not authoritative for new runtime bindings |

`ai_mcp_server_profile` and `ai_mcp_server_tool` were found to be orphaned
`__pycache__`-only directories (no real doctype files, untracked, absent from git
history) and were already deleted on 2026-08-19 — no migration needed.

---

## Related Documentation

- [FRAPPE_ASSISTANT_CORE_INTEGRATION.md](./FRAPPE_ASSISTANT_CORE_INTEGRATION.md) - Architecture details
- [MCP_INTEGRATION_SETUP_GUIDE.md](./MCP_INTEGRATION_SETUP_GUIDE.md) - Setup guide
- [DOCTYPE_CLEANUP_PLAN.md](./DOCTYPE_CLEANUP_PLAN.md) - Cleanup overview
