# ADR 0018 — Audit of Agno usage decisions against Agno's documented patterns

**Status:** Accepted
**Date:** 2026-09-06
**Deciders:** Sri Harsha Dabbiru

---

## Context

The architecture in ADRs 0001–0017 was designed before its author had working knowledge
of Agno or agentic-AI engineering generally. That doesn't make the decisions wrong, but it
means they were never checked against how Agno itself expects to be used — only against
Frappe's constraints (permissions, `safe_exec`, source-of-truth ownership). This ADR is
that missing check: every Agno-touching decision in the current implementation, compared
against Agno's own documentation (retrieved via Context7, library `/agno-agi/docs`), area
by area.

This audit does not revisit the Frappe-as-source-of-truth invariant (ADR 0001, ADR 0017,
§7.1 of the architecture spec) — that decision is sound and pre-existing ADRs justify it
independently of Agno. Nothing here proposes a second persistent store (e.g. giving Agno
its own SQLite `db=`) as a fix for anything; where a fix is needed, it is a read-through
cache in front of Frappe, not a competing store.

---

## Findings by area

### 1. Session / State Management — Sound (architecture), cache-worthy (performance)

**Current:** `AgentBuilder.build()` (`frappe_ai/service/builder.py:150-166`) constructs
`Agent(..., db=None, add_history_to_context=False, telemetry=False)` fresh on every chat
turn. `AISession.build_prompt_messages()`
(`frappe_ai/frappe_ai/doctype/ai_session/ai_session.py:225-271`) reloads and reconstructs
the full transcript from the `AI Session Message` child table every turn, including memory
block injection and attachment handling, done in Python rather than via Agno.

**Agno's pattern:** `db=<SqliteDb|PostgresDb|...>` with `add_history_to_context=True` and
`num_history_runs=N` is the documented default. Agno also documents
`store_history_messages=False` for "use history in context, don't let Agno's db be the
store of record" — the closest built-in match to what this app wants, though it still
requires wiring an Agno `db=`, which this app has already ruled out (ADR 0001/0017).

**Verdict:** The no-Agno-db decision stands — Agno's own accommodation for
"read without write" still assumes an Agno-owned `db`, so it isn't a full match anyway.
The real cost is the O(N)-per-turn / O(N²)-per-session transcript rebuild, which is a
caching problem independent of Agno — Agno's own `db=` pattern has the same growth curve
for unsummarized history. **No architecture change.** Read-through cache on session
message reload is tracked as a separate performance workstream.

### 2. Tools / Dispatch — Sound

**Current:** Every tool is an Agno `Function` (`frappe_ai/service/builder.py:215-221`)
whose entrypoint dispatches over HTTP to Frappe (`dispatch_tool`/`dispatch_plugin_tool`),
never executing locally; `skip_entrypoint_processing=True` bypasses Agno's docstring/type-
hint schema inference since the JSON Schema already comes from Frappe.

**Agno's pattern:** A plain function (schema inferred from type hints/docstring) or a
`Toolkit` subclass with `@tool`-decorated methods — both assume the function *is* the
executable logic, in-process.

**Verdict:** Correct, deliberate adaptation of Agno's tool primitive to a constraint Agno's
docs don't anticipate — tool execution living in a separate authorization domain
(ADR 0003). **No action.**

### 3. MCP Integration — Worth reconsidering

**Current:** `_build_mcp_tools` (`frappe_ai/service/builder.py:223-291`) constructs a
fresh `MCPTools(...)` every turn, for every agent with MCP connections bound, without ever
calling `.connect()`.

**Agno's pattern:** Documented directly in `tools/mcp/overview.mdx`: passing `MCPTools` to
an `Agent` without manual connection "triggers automatic connection and closure on each
run, which may impact performance." The documented fix: `await mcp_tools.connect()` once,
reuse across multiple runs, `.close()` at session end. Agno even documents a
`refresh_connection=True` flag for the rare case where reconnecting every run is actually
wanted.

**Verdict:** Confirmed cost, confirmed fix, no architecture risk. Every MCP-backed agent
currently pays a full handshake + tool-discovery round trip on every chat turn.

**Next step:** Cache a connected `MCPTools` instance per MCP connection (or per session, if
cross-user sharing of the same connection is undesirable) in the FastAPI process. Connect
on first use, reuse across turns, close on idle timeout or process shutdown. This is a
read-through cache over a resource, not a new store — Frappe still owns `AI Agent MCP
Connection` config, and losing a cached connection on restart is harmless (reconnects on
next use).

### 4. Memory — Reinvented, but for a defensible reason

**Current:** `frappe_ai/memory/memory.py` — `build_memory_block()` runs several
`frappe.get_all` calls every turn against `AI Agent Memory`, then does BM25 selection
(backed by a vectorless LanceDB table) and splices a formatted `<agent_memory>` block into
the system message. Writes go through a custom `update_memory` tool with a
100-memory-per-bucket cap.

**Agno's pattern:** `enable_agentic_memory=True` + `MemoryManager` — the agent decides when
to store/update/delete memories via built-in tool calls, scoped by `user_id`, retrieved and
injected automatically, persisted through the agent's own `db=`.

**Verdict:** This is the clearest "Agno already has this" case in the audit — but Agno's
`MemoryManager` is designed to persist through Agno's own `db=`, which this app has
deliberately not adopted. Swapping to it would require writing a Frappe-backed `db=`
adapter, which Agno doesn't document a pattern for and which is out of scope here (it would
reintroduce exactly the dual-store question ADR 0001/0017 already settled). The
Frappe-specific policy this app needs — Agent/User scoping, a hard size cap, prompt-
injection hardening on injected memory content — isn't something `MemoryManager` provides
out of the box either.

**Next step:** Keep the custom `AIAgentMemory` doctype. Cache `_active_memories(agent,
user)` reads (invalidate on `AI Agent Memory` write) as a performance fix. Do not migrate
to Agno's `MemoryManager` unless a future phase commits to building a Frappe-backed `db=`
adapter — a materially bigger undertaking than anything else in this audit.

### 5. Knowledge / RAG — Sound (architecture), cache-worthy (performance)

**Current:** `frappe_ai/knowledge/retriever.py` calls `embed_texts([query])` on every
retrieval call, queries a hand-rolled LanceDB store, then hydrates chunk text back from
MariaDB (`AI Knowledge Chunk`) since LanceDB only stores vectors + ids.

**Agno's pattern:** `Knowledge` wraps a vector db (e.g. `LanceDb`) directly; `Agent(...,
search_knowledge=True)` triggers automatic retrieval, with text/metadata living alongside
vectors in the same store — no separate hydration step.

**Verdict:** MariaDB-as-source-of-truth for chunk text (ADR 0017: "MariaDB authoritative,
LanceDB disposable") is a deliberate, already-justified split that Agno's native `Knowledge`
doesn't offer — Agno's vector store is normally the only copy of the chunk. Giving that up
to match Agno's default would mean losing rebuildability. **Architecture stands.**

**Next step:** `embed_texts([query])` has no cache in front of it anywhere in the call path
(confirmed: `embedder.py` re-embeds on every call, including `retrieve_attachments`).
Add a read-through cache keyed on `sha256(query_text)` → embedding vector. Cache miss just
re-embeds — no change to source-of-truth semantics.

### 6. Multi-Agent / Team Patterns — Missing / dangling field

**Current:** `AI Agent.agent_type` is a Select field (`Agent`/`Team`, default `Agent`), but
nothing in `frappe_ai/service/` or `frappe_ai/api/` reads it, and no `agno.team` import
exists anywhere in the app. `AgentBuilder.build()` unconditionally builds a plain
`agno.agent.Agent`.

**Agno's pattern:** `Team` objects support routing/broadcast/coordinate patterns for
multi-agent delegation; Agno's own guidance is to prefer a single agent when the task fits
one domain.

**Verdict:** Not a divergence from Agno's guidance — there's no delegation use case
established yet to compare against. It's a dangling capability implied by a DocType field
that does nothing.

**Next step:** Documentation hygiene, not implementation: mark `agent_type` as not-yet-wired
in `003-doctype-reference.md`, or remove the field, so selecting "Team" doesn't silently
produce ordinary Agent behavior.

### 7. Confirmation / Human-in-the-Loop Flow — Sound given the constraint, fragile in detail

**Current:** A fully custom `PendingConfirmation` exception (`frappe_ai/service/
builder.py:13-90`), caught by the run loop, recovered via a string marker
(`PENDING_CONFIRMATION_MARKER`) because Agno's `Function.aexecute` swallows plain
exceptions into `ToolExecution(tool_call_error=True, ...)` rather than propagating them.

**Agno's pattern:** `@tool(requires_confirmation=True)`, then after a run: iterate
`run_response.active_requirements`, call `.confirm()`/`.reject()`, call
`agent.continue_run(run_id=..., requirements=...)`. All of Agno's documented examples pair
this with `db=` (SQLite/Postgres) because `continue_run` needs the paused `RunOutput`
persisted somewhere between the two calls.

**Verdict:** The app's own code comment is accurate — Agno's native HITL flow is designed
to compose with Agno's own persistence, which this app doesn't use. This isn't "Agno's HITL
is deficient," it's a direct, unavoidable consequence of the Frappe-as-source-of-truth
decision (Finding 1) — not an independent gap to close. The custom marker-string mechanism
works today but depends on the exact way `Function.aexecute` swallows exceptions in the
currently pinned Agno version — that behavior is an implementation detail, not a stable
public contract.

**Next step:** No redesign (would require adopting Agno's `db=`, out of scope). Pin the
Agno version tightly and add a regression test that fails loudly if a future Agno upgrade
changes how a plain exception raised from an entrypoint is surfaced, since the whole
recovery path depends on that specific behavior.

### 8. Structured Output / Reasoning / Markdown Config — Sound

**Current:** `Agent(..., markdown=agent_cfg["markdown"], reasoning=agent_cfg["reasoning"])`
— direct pass-through of two `AI Agent` DocType checkboxes to documented Agno `Agent`
constructor kwargs.

**Verdict:** No divergence found. **No action.**

### 9. Observability / Telemetry — Sound (telemetry), fragile in detail (diagnostics)

**Current:** `telemetry=False` set unconditionally in every build. Separately, `chat.py`'s
`_AgnoDiagnosticHandler` attaches directly to Agno's internal `"agno"` logger (bypassing its
`propagate=False`) to recover exception detail that would otherwise be silently dropped,
forwarding it into Frappe's Error Log.

**Agno's pattern:** The `telemetry` kwarg controls a small anonymous usage-analytics
payload sent to Agno's own servers — unrelated to application observability. Real Agno
observability is a separate, fully opt-in OpenTelemetry integration under `AgentOS`,
emitting spans per model call/tool execution to a tracing backend.

**Verdict:** `telemetry=False` is the right call for a multi-tenant product handling
customer data — no anonymous run metadata should leave the process by default. The app's
real observability need (surfacing model/tool failures) is already met, but via a
logger-attachment workaround coupled to an internal, non-public Agno logging detail — the
same fragility pattern as Finding 7.

**Next step:** No action required now. If richer observability is wanted later, Agno's
OpenTelemetry `AgentOS` tracing is the documented non-hacky path — but that's a genuinely
new integration, not a low-risk fix, so it's future-phase work, not part of this audit's
output.

---

## Summary

| Area | Verdict | Action |
|---|---|---|
| Session/State | Sound (arch) / cache-worthy (perf) | Read-through cache on transcript reload |
| Tools/Dispatch | Sound | None |
| MCP | **Worth reconsidering** | Cache connected `MCPTools` per connection; connect once, reuse, close on idle |
| Memory | Reinvented, justifiably | Cache `_active_memories` reads; don't migrate to Agno `MemoryManager` without a Frappe `db=` adapter |
| Knowledge/RAG | Sound (arch) / cache-worthy (perf) | Cache embeddings by `sha256(query_text)` |
| Multi-Agent/Team | Missing/dangling | Mark `agent_type` unimplemented in docs, or remove the field |
| Confirmation Flow | Sound given constraint, fragile in detail | Pin Agno version; add regression test for exception-swallow behavior |
| Structured Output/Reasoning | Sound | None |
| Observability | Sound (telemetry) / fragile (diagnostics) | None now; Agno OpenTelemetry `AgentOS` tracing is the future path if needed |

**No finding in this audit calls for reopening ADR 0001 or ADR 0017.** Every "worth
reconsidering" or "reinvented" item resolves within a read-through-cache pattern in front
of the existing Frappe-as-source-of-truth architecture, not a change to that architecture.

---

## Consequences

### Positive
- Confirms the load-bearing decisions (tools-execute-in-Frappe, MariaDB-authoritative
  knowledge, no second persistent store) are sound independent of Agno familiarity —
  they were right for Frappe-specific reasons the audit re-derives from Agno's own docs.
- Produces concrete, low-risk next steps (MCP connection reuse, two caches, one doc fix,
  one regression test) instead of a redesign.
- Surfaces two "coupled to Agno internals" fragility points (confirmation-flow exception
  swallowing, diagnostic log handler) that were not previously flagged anywhere.

### Negative
- The Memory and Confirmation Flow findings mean this app cannot cleanly adopt two of
  Agno's more polished native features (`MemoryManager`, native HITL pause/resume) without
  first solving the harder problem of a Frappe-backed Agno `db=` adapter — deferred, not
  solved, by this audit.

---

## References

- ADR 0001 — Agno + FastAPI over Frappe-native
- ADR 0003 — Tools execute inside Frappe
- ADR 0017 — MariaDB authoritative, LanceDB disposable
- [001 — Architecture §7.1, §8](../specifications/001-architecture.md)
- [003 — DocType Reference](../specifications/003-doctype-reference.md)
- Agno documentation, via Context7 (`/agno-agi/docs`): `agents/usage/agent-with-storage.mdx`,
  `state/agent/session-state-basic.mdx`, `examples/agents/state-and-session/session-options.mdx`,
  `tools/creating-tools/overview.mdx`, `tools/tool-decorator/tool-decorator-on-class-method.mdx`,
  `tools/mcp/overview.mdx`, `tools/mcp/multiple-servers.mdx`,
  `agents/memory-and-learning/memory-manager.mdx`, `memory/agentic-memory.mdx`,
  `knowledge/teams/distributed-rag-lancedb.mdx`, `teams/overview.mdx`,
  `examples/agents/human-in-the-loop/confirmation-required.mdx`,
  `examples/basics/human-in-the-loop.mdx`, `use-cases/document-processing/human-routing-and-eval.mdx`,
  `telemetry.mdx`, `features/observability.mdx`,
  `examples/integrations/observability/logfire-via-openinference.mdx`
