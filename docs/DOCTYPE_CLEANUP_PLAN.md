# Which DocTypes are legacy, and what can be removed

A short guide to the DocTypes that look removable but are not (yet), so nobody deletes one by mistake.

## The rule

Every DocType in this app is in use. The only ones expected to go are the **legacy tool records**, and only after Assistant Core (FAC) fully replaces them. Until then they are load-bearing.

## Legacy tool records: keep for now

| DocType | Why it is still needed | Retirement gate |
|---|---|---|
| `AI Tool` | The ten built-in tools (`find_doctypes`, `describe`, `read`, `search_knowledge`, `update_memory`, `create`, `update`, `delete`, `run_action`, `execute`) are `AI Tool` rows created on `migrate`. `dispatch_tool`, `lib/resolver.py` and the frontend's tool summaries read them. Script tools also live here | Every production `AI Tool` has a verified exact FAC equivalent and agents are bound to it through `AI Agent Plugin Tool` |
| `AI Agent Tool` (`AI Agent.tools`) | Binds `AI Tool` rows to an agent; `_ensure_knowledge_search_tool` uses it | Same gate |
| `AI Agent Tool Config` | Compatibility metadata for the older tools table | Same gate |
| `AI MCP Tool` | Not legacy: it stores the tools discovered on an MCP connection | Stays as long as MCP connections exist |

At run time, only FAC tools (`AI Agent Plugin Tool`) are sent to the service. `AI Tool` is reached only through the legacy `dispatch_tool` path and the resume path for tools that are not FAC tools.

**Do not make `AI Tool` an alias of a FAC tool.** The two would carry duplicate names, descriptions, enabled flags, implementations and confirmation settings that drift apart. The long-term link is the agent's `AI Agent Plugin Tool.fac_tool`.

## Everything else stays

`AI Agent`, `AI Agent Knowledge Base`, `AI Agent MCP Connection`, `AI Agent Memory`, `AI Agent Plugin Tool`, `AI FAC Tool`, `AI Knowledge Base/Source/Chunk`, `AI MCP Connection`, `AI Model`, `AI Provider`, `AI Run`, `AI Session` and its child tables, `AI Settings`, `AI Trigger`. Triggers are used by other apps (for example the tender automation app); memory and knowledge are active features.

## Already removed

The DocTypes `AI MCP Server Profile` and `AI MCP Server Tool` (from an abandoned proposal, [006](specifications/006-dynamic-mcp-server-profiles.md)) were deleted; only stray compiled-bytecode folders remained and those are gone too.

## Checklist before deleting any legacy DocType

1. Search the code for the DocType name and its fields (`grep -rn "AI Tool" frappe_ai/`).
2. Confirm the migration report (`api/migration.py`) shows no unmatched or ambiguous tools on every site.
3. Verify the affected workflows end to end, with a real model, through FAC.
4. Take a backup, run `bench migrate` on a copy, and check there are no missing-doctype errors.

The detailed phase list and status is in [007](specifications/007-mcp-integration-and-cleanup.md) and the [progress note](progress/ai-tool-retirement-via-assistant-core.md).
