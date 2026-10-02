# Frappe Assistant Core (FAC) integration

**Frappe Assistant Core** (`frappe_assistant_core`) is a separate Frappe app. It keeps a **registry of tools** that apps contribute and enforces who may use each one. `frappe_ai` agents use those tools. This page explains how the two fit together and what is left to do.

## Why FAC

- **One catalogue of Frappe tools.** An app writes a tool once; it can then be used by `frappe_ai` agents and by other AI clients that speak to Assistant Core.
- **One permission story.** FAC decides which tools are enabled and which roles may use them. `frappe_ai` additionally runs every call as the chat user.
- **Less duplicate code** in `frappe_ai` than keeping its own tool system.

## How it works today

```
BaseTool class in an app  ──registered in hooks.py (assistant_tools)──►  FAC registry
                                                                              │
Admin binds it to an agent (AI Agent Plugin Tool row)                         │
                                                                              ▼
get_run_config → _resolve_agent_plugin_tools:   asks the registry, for THIS user, which bound
                                                  tools are enabled and accessible
                                                              │
Service builds a function per tool; the model calls it        │
                                                              ▼
Frappe dispatch_plugin_tool: checks run, user, approval, budgets, sets the user,
                             then get_tool_registry().execute_tool(...)
```

`frappe_ai` registers four tools of its own this way (`assistant_tools/native.py`): `execute`, `run_action`, `search_knowledge`, `update_memory`.

Context-sensitive tools get server-side arguments the model cannot set. For example `search_knowledge` is limited to the run's agent's knowledge bases, `update_memory` writes to the run's agent, and `load_full_document_text` sizes its output for the run's model. `dispatch_plugin_tool` fills these from the saved run (`_resolve_plugin_context`), ignoring anything the model supplied under those names.

## Who owns what

| Concern | Owner |
|---|---|
| Tool code, schema, category, enabled flag, role access | FAC and the app that contributes the tool |
| Which tools an agent uses, and whether each needs approval | `frappe_ai`: `AI Agent Plugin Tool` rows |
| Running the call as the user, approvals, budgets | `frappe_ai` (`api/dispatch.py`) |
| Conversations, runs, knowledge, memory, streaming | `frappe_ai` |

Run configuration includes tool schemas but not credentials for FAC tools; they are in-process calls. Use `frappe_ai.api.fac_tools.sync_fac_tools` (also run after `migrate`) to copy the registry into the `AI FAC Tool` list shown in the Desk.

## Two ways to reach FAC

| | Direct (preferred) | Through MCP |
|---|---|---|
| Mechanism | Plugin Tool row; in-process call | `AI MCP Connection` to FAC's MCP endpoint |
| Identity | The chat user | One credential for all runs unless you build a delegated or signed bridge |
| Approval and budgets enforced by Frappe | Yes | No |

Do not use a shared privileged API key for agents that can change data. If you must use MCP, give the account read-only access.

## What `frappe_ai` keeps for itself

Chat and streaming, sessions, runs and audit, knowledge and memory, triggers, approvals and budgets, and the Desk UI. These are not part of FAC.

## Status and open work

Done: direct bindings and dispatch, native tool wrappers, compatibility migration (`AI Tool` → plugin tools) with a report of what matched, tender agents bound directly.

Open: parity checks for `execute` and `run_action`, verifying the tender workflows end to end through direct FAC before removing the tender MCP connection, approval and budget accounting for MCP calls, and a delegated per-user credential for MCP. Details: [007](specifications/007-mcp-integration-and-cleanup.md) and the [progress note](progress/ai-tool-retirement-via-assistant-core.md).

## Rules when FAC is unavailable

A direct tool the registry does not offer is simply not given to the model for that run. An MCP connection that cannot connect is skipped. In both cases the run continues with the remaining tools.
