# Progress — Agno usage audit (ADR 0018)

## Status

Audit complete. ADR 0018 written and accepted. No implementation started yet — this was a
review deliverable, not a code change.

## Current phase

Review complete. Awaiting decision on which "next step" items to schedule as actual work.

## Completed

- Read `frappe_ai`'s full Agno-touching surface: session/state, tool dispatch, MCP,
  memory, knowledge/RAG, multi-agent fields, confirmation flow, structured output config,
  observability/telemetry.
- Cross-checked each area against Agno's own documentation (via Context7, `/agno-agi/docs`).
- Classified each decision as Sound / Worth reconsidering / Reinvented, with file:line
  citations and doc citations.
- Wrote [ADR 0018](../decisions/0018-agno-usage-audit.md) capturing the findings.

## In progress

Nothing — the audit itself is done. The items below are candidate follow-up work, not
started.

## Findings requiring follow-up (not yet scheduled)

1. **MCP connection reuse** (Worth reconsidering) — `_build_mcp_tools` rebuilds and
   auto-connects/closes `MCPTools` every turn. Confirmed by Agno's own docs as a
   performance anti-pattern with a documented fix. Lowest-risk, highest-confidence item
   in the audit.
2. **Session message reload caching** (perf) — `build_prompt_messages()` reloads the full
   transcript every turn; read-through cache candidate.
3. **Active-memory read caching** (perf) — `build_memory_block()`'s `frappe.get_all` calls
   repeat every turn; read-through cache candidate.
4. **Embedding cache** (perf) — `embed_texts([query])` has no cache anywhere in the
   retrieval path; content-hash-keyed cache is a clean win.
5. **`agent_type` field cleanup** (doc hygiene) — Select field implies Team support that
   doesn't exist in code. Mark unimplemented or remove.
6. **Confirmation-flow fragility** (hardening) — `PendingConfirmation` recovery depends on
   an unpinned Agno internal exception-swallowing behavior. Needs a version pin + a
   regression test, not a redesign.

## Blockers

None. This was research/documentation only.

## Remaining work

Decide with the user which of the six follow-up items above (if any) become actual
implementation phases, plan each in phases per the standard workflow, and update this
progress file as each is picked up.
