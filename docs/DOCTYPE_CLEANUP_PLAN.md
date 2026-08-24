# frappe_ai DocType Cleanup Plan

> Plan to remove genuinely unused DocTypes, and to correct an earlier version of this
> plan that assumed MCP had already replaced the builtin tool system.

---

## Executive Summary

An earlier version of this document proposed removing `ai_tool`, `ai_agent_tool`, and
`ai_mcp_tool` on the assumption that MCP (via Assistant Core) already provided
equivalents. **A code audit (2026-08-19) found that assumption false**: `AI Tool` is
still the mechanism behind every builtin tool (`describe`, `read`, `execute`,
`search_knowledge`, …), and `AI Agent` carries `tools` (AI Tool-based) and
`mcp_connections` (MCP-based) as two **parallel, coexisting** mechanisms — MCP was
added alongside the tool system, not in place of it. No MCP-side equivalents of the
builtins exist yet.

The only genuinely dead artifacts found are two `__pycache__`-only directories left
over from doctypes that were deleted from disk (properly, via git — they are untracked
and absent from git history) but never had their compiled bytecode cleaned up.

---

## Current State Analysis

### DocTypes in frappe_ai (verified 2026-08-19)

```
frappe_ai/frappe_ai/doctype/
├── ai_agent                    ✅ KEEP - Core agent definition
├── ai_agent_knowledge_base     ✅ KEEP - Agent knowledge linking
├── ai_agent_mcp_connection     ✅ KEEP - MCP connection to agent linking
├── ai_agent_memory             ✅ KEEP - Agent memory feature
├── ai_agent_tool                ✅ KEEP (load-bearing) - AI Agent.tools child table;
│                                  binds builtin/custom AI Tool rows to an agent
├── ai_knowledge_base           ✅ KEEP - Knowledge feature
├── ai_knowledge_chunk          ✅ KEEP - Knowledge feature
├── ai_knowledge_source         ✅ KEEP - Knowledge feature
├── ai_mcp_connection            ✅ KEEP (FIXED) - MCP connection definition
├── ai_mcp_server_profile        🗑️ DELETE - orphaned __pycache__ only, not a real
│                                  doctype (no .json/.py, untracked, absent from git
│                                  history); already effectively gone
├── ai_mcp_server_tool           🗑️ DELETE - same as above: orphaned __pycache__ only
├── ai_mcp_tool                  ✅ KEEP (load-bearing) - AI MCP Connection.tools
│                                  child table (ai_mcp_connection.json:127); used in
│                                  tests/test_mcp.py
├── ai_model                   ✅ KEEP - LLM configuration
├── ai_provider                ✅ KEEP - API provider credentials
├── ai_run                     ✅ KEEP - Run tracking
├── ai_session                 ✅ KEEP - Chat sessions
├── ai_session_attachment       ✅ KEEP - File attachments
├── ai_session_message          ✅ KEEP - Session messages
├── ai_settings                ✅ KEEP - App settings
├── ai_tool                      ✅ KEEP (load-bearing) - backs dispatch.py,
│                                  builtins.py, resolver.py, service.py, frontend.py,
│                                  api.py, mcp.py; the 10 builtin tools have no MCP
│                                  equivalent
└── ai_trigger                ✅ KEEP - Triggers (used by other apps)
```

---

## What Can Actually Be Removed

### Category 1: Dead artifacts (safe now)

| DocType | Reason | Impact |
|---------|--------|--------|
| `ai_mcp_server_profile` | Only a `__pycache__` directory remains on disk; no `.json`, `.py`, or `__init__.py`; untracked and absent from git history | None — delete the leftover directory |
| `ai_mcp_server_tool` | Same as above | None — delete the leftover directory |

### Category 2: Previously proposed, now retracted

| DocType | Why it was wrongly listed | Current status |
|---------|---------------------------|-----------------|
| `ai_tool` | Assumed MCP already replaced custom tools | **Load-bearing.** Powers `dispatch.py` tool execution, `builtins.py`'s 10 builtin tools, `resolver.py` schema derivation. No MCP replacement exists. |
| `ai_agent_tool` | Assumed MCP connections replace this | **Load-bearing.** Is `AI Agent.tools`; used in `ai_agent.py` `before_insert` / `_ensure_knowledge_search_tool`. Coexists with `mcp_connections`, not superseded by it. |
| `ai_mcp_tool` | Assumed tools are only discovered dynamically, never stored | **Load-bearing.** Is `AI MCP Connection.tools`, the actual storage for discovered MCP tool metadata. |

A real migration off `ai_tool`/`ai_agent_tool` would require building MCP-side
equivalents for every builtin tool first, then migrating each agent, then verifying
parity — a substantial feature project in its own right, not a cleanup. It is **not
scheduled**; see "Future work" below.

---

## Cleanup Sequence (revised)

### Step 1: Remove orphaned `__pycache__` directories — completed 2026-08-19

The two directories were already removed from the working tree during the 2026-08-19
audit. No code, fixture, or `hooks.py` changes were needed — nothing references these
names outside of the real `AI MCP Tool`/`AI MCP Connection` DocTypes.

### Step 2: Verify — remaining environment check

- [ ] `bench build` / `bench migrate` succeed with no missing-doctype errors
- [x] The two directories are absent from the working tree
- [x] No tracked file references the deleted paths

---

## Future Work — now scheduled

**2026-08-19 update:** the migration this section called for is now planned and
underway. See
[007 — MCP Integration & DocType Cleanup](specifications/007-mcp-integration-and-cleanup.md)
for the verified, phased plan (Phases 2-7: mutating builtins, `execute` sandbox
parity, `frappe_ai`-native tools as Assistant Core contributions,
`tender_automation`-native tools as Assistant Core contributions, `run_action` +
full audit, then actual DocType retirement). That spec supersedes the four-step
sketch previously here — it is based on a live audit of Assistant Core's actual
tool catalogue rather than an assumption that generic equivalents exist.

---

## Notes

- Knowledge features (`ai_knowledge_*`) remain — actively used
- Triggers (`ai_trigger`) remain — used by `tender_automation`
- Memory features remain — used by agents
- `ai_mcp_connection` is fixed and required
- This document previously (pre-2026-08-19) recommended removing `ai_tool`,
  `ai_agent_tool`, and `ai_mcp_tool`. That recommendation was based on an incorrect
  assumption and has been retracted above.
