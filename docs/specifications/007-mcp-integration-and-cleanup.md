# 007 — External tools: MCP and Assistant Core

How tools that do not live in this app reach an agent, and what is still unfinished.

## The three kinds of tools an agent can have

| Kind | Where it is defined | How a call runs | Approval enforced by Frappe? | Counted in run budgets? |
|---|---|---|---|---|
| **Assistant Core (FAC) tool**, bound with `AI Agent Plugin Tool` | A `BaseTool` class in any installed app, found through the FAC registry | Service → Frappe `dispatch_plugin_tool` → registry, **as the user** | **Yes** | **Yes** |
| **Built-in / script `AI Tool`** (legacy path) | `AI Tool` record | Service → Frappe `dispatch_tool`, as the user | **Yes** | **Yes** |
| **MCP tool**, from `AI Agent MCP Connection` | An MCP server (remote URL or a local program) | The **service** talks to the MCP server directly | **No** (Frappe never sees the call) | **No** |

The first row is the path new work should use. The third row is useful but sits outside Frappe's checks: the MCP server decides what the call may do, using whatever credentials the connection holds. Treat an MCP connection as a trusted integration, not as something that inherits the user's permissions.

## How an agent gets its tools

`get_run_config` (`api/service.py`) builds two lists for the run:

- `_resolve_agent_plugin_tools(agent, user)`: for each enabled `AI Agent Plugin Tool` row, asks Assistant Core's registry whether the tool is enabled **and accessible to that user** and, if so, includes its name, description, JSON Schema, and the row's `requires_confirmation` flag (source `"fac"`).
- `_resolve_agent_mcp_connections(agent, ...)`: each enabled `AI MCP Connection` bound to the agent, with command or URL, environment, optional `include_tools` list and credentials.

The service (`service/builder.py`) turns the first list into functions that call back into Frappe, and the second into Agno `MCPTools` clients (`stdio`, `streamable-http` or `sse`). A connection currently marked not connected is skipped. If a tool is exposed both directly and through MCP, the direct one wins (the MCP copy is removed from `include_tools`).

## MCP connections

Fields and formats are in [003](003-doctype-reference.md#ai-mcp-connection); step-by-step setup is in the [MCP setup guide](../MCP_INTEGRATION_SETUP_GUIDE.md).

Things to know:

- **`stdio` connections run a program on the server.** Only System Manager can create or edit connections. `command` is a single executable; arguments are a separate JSON list; environment values must be strings. Anything else is rejected on save.
- **Health checks** (`api/mcp.py`) perform a real handshake and tool listing, every 5 minutes and on the form's *Test Connection* button, and store the result and discovered tools on the connection (`AI MCP Tool` rows).
- **Authentication for `streamable-http`:** the service sends `Authorization: token <api_key>:<api_secret>`. Both fields are Password fields.
- **Identity.** An MCP server authenticates *the credential*, not the chat user. With one shared API key, every run looks like the same user to that server and per-user filtering is lost. Use a restricted, read-only credential, or a delegated per-user credential, before exposing tools that change data. A signed acting-user bridge on the Assistant Core side is the other option; it is not built yet.

## Status of the move to Assistant Core

The goal is one tool path (a `BaseTool` in the owning app → FAC configuration → `AI Agent Plugin Tool` → dispatch) and to stop using `AI Tool` for new work. Done:

- Direct bindings (`AI Agent Plugin Tool`) and `dispatch_plugin_tool`, including run scope for `search_knowledge`, `update_memory` and `load_full_document_text`.
- `get_run_config` offers FAC tools only; the legacy `AI Tool` list is no longer sent to the service.
- A one-time migration/reporting step (`api/migration.py`, run after `migrate`) matches `AI Tool` rows to FAC tools by name.
- The four native tools `execute`, `run_action`, `search_knowledge`, `update_memory` are wrapped as Assistant Core tools (`assistant_tools/native.py`).
- The three tender agents are bound directly to FAC tools, with an MCP connection kept as a fallback.

Still open:

- Prove that every production `AI Tool` has an exact FAC equivalent, then retire the legacy runtime path. `AI Tool` and `AI Agent Tool` stay as compatibility records until then.
- Compare `execute` in its FAC wrapper with the original sandbox and add parity tests; resolve `run_action`.
- Verify the tender workflows (spec review, historical match, SAP match) through direct FAC, then remove the tender MCP connection.
- Decide how remote MCP calls get approval and budget accounting like direct calls.
- A full end-to-end test with a real model and FAC tool calls (blocked on model credentials in the test environment).

More detail: [Assistant Core integration](../FRAPPE_ASSISTANT_CORE_INTEGRATION.md), [DocType cleanup plan](../DOCTYPE_CLEANUP_PLAN.md), [progress note](../progress/ai-tool-retirement-via-assistant-core.md).
