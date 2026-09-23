# frappe_ai vs huf — Detailed Architectural Comparison
## What frappe_ai should adopt from huf

This document compares the two apps at the level of their explicit architectural
decisions and identifies concrete improvements for `frappe_ai`, justified against
frappe_ai's own ADRs and known gaps.

---

## Architecture Snapshot

| Dimension | frappe_ai | huf |
|---|---|---|
| **Agent framework** | Agno (`agno>=2.8`) in FastAPI sidecar | Custom `GraphExecutor` + LiteLLM in RQ workers |
| **LLM routing** | OpenAI-compatible transport only (ADR 0014) | LiteLLM execution-level router (100+ providers) |
| **Provider support** | Any OpenAI-compatible API | Any LiteLLM-supported provider (Anthropic native, Google native, Ollama, etc.) |
| **Prompt caching** | None | Anthropic / OpenAI / Google (via LiteLLM) |
| **Streaming** | Browser → FastAPI direct SSE (ADR 0004) | Browser → Frappe gunicorn SSE (`agent_stream_renderer`) |
| **Tool dispatch** | HTTP callback: FastAPI → Frappe → `frappe.set_user` | In-process RQ worker, no separate sidecar |
| **Mutation budgets** | `SELECT … FOR UPDATE` per-run (ADR 0008) | `ExecutionProfile` (infrastructure only) |
| **HITL confirmation** | Per-tool `requires_confirmation`, inline `ConfirmCard` | `Agent Execution Approval` DocType (separate flow) |
| **Knowledge backends** | LanceDB only (ADR 0002) | 9 pluggable backends |
| **Knowledge source types** | Text / File / URL / DocType with watermark | Text / File / URL only |
| **Knowledge deduplication** | `content_hash` per chunk (ingest.py) | None |
| **Memory** | `AI Agent Memory` (keyword relevance, 20-cap injection) | `Memory Policy` + `Memory Record` (scoped, TTL, vector/keyword) |
| **MCP connection** | New `MCPTools` per turn — reconnects every call (ADR 0018 Finding 3) | `mcp_session_pool()` — per-run session reuse via `contextvars.ContextVar` |
| **Wildcard doc hooks** | `"*"` for all 5 events (known performance issue) | `"*"` for all 14 events (same issue) |
| **Gateways** | None | WhatsApp, Telegram, Slack, Discord, Email, Teams, Google Chat, Messenger, Instagram |
| **Multi-agent / Teams** | `agent_type` field exists, does nothing (ADR 0018 Finding 6) | Sequential orchestration (`Agent Orchestration`) |
| **Flows / Procedures** | None (ADR 0007 — failure over durable execution) | Full graph executor (12 node types, deterministic procedures) |
| **Skills** | None | Bundled capability packs (tools + knowledge + prompts + MCP) |
| **Reasoning controls** | `reasoning: bool` only | `reasoning_mode`, `reasoning_effort`, `reasoning_budget_tokens`, `reasoning_summary` |
| **Batch API** | None | Anthropic, OpenAI, Gemini batch |
| **Voice** | None | ElevenLabs + LiteLLM realtime WebSocket |
| **Analytics** | `feedback_rating` / `feedback_comment` on `AI Run` | `Agent Run Analytics Rollup`, token/cost analytics dashboard |
| **Config snapshot** | Full `config_snapshot` JSON on `AI Run` ✓ | Model + provider only |
| **DocType knowledge (live records)** | ✓ Watermark incremental sync + `content_hash` dedup | Not yet implemented |
| **Desk panel injection** | ✓ `app_include_js` in every desk page | Separate SPA at `/huf` |
| **Provider capability testing** | ✓ Config-time model capability tests (ADR 0015) | `supports_*` flags on `AI Model`, no config-time test |

---

## Decision-by-Decision Analysis

### ADR 0001 — Agno + FastAPI over Frappe-native

**frappe_ai's position:** The LLM loop runs in a FastAPI sidecar because Agno's
async coroutine loop is incompatible with Frappe's synchronous Gevent workers.
`frappe.get_db()` and `frappe.set_user()` require a Frappe request context; Agno's
`agent.arun()` is a long-lived async generator that cannot hold one.

**huf's approach:** No sidecar. LLM calls run inside Frappe RQ workers, which have
a full request context. LiteLLM + httpx are async-compatible in a worker context.

**Assessment for frappe_ai:** The two-process split is the right call given Agno.
It is not a weakness — it cleanly separates concerns and prevents slow LLM calls from
blocking Frappe web workers (huf's streaming via gunicorn does not have this
property). **No change.** If frappe_ai ever migrates away from Agno, the RQ-worker
approach becomes viable, but that would be a large undertaking.

**One thing to adopt from huf:** The FastAPI sidecar exposes only one route (`POST
/stream/{run}`). huf's `agent_stream_renderer` is also a single endpoint. Neither
has a problem here. But huf's streaming architecture has one gap frappe_ai already
solved: **huf's SSE runs through gunicorn workers**. frappe_ai's SSE runs directly
from FastAPI, never consuming a Frappe worker slot. This is an advantage frappe_ai
should document and preserve.

---

### ADR 0002 — LanceDB as the Vector Store

**frappe_ai's position:** LanceDB is embedded (no external service), written in Rust
(fast), and disposable (ADR 0017 — MariaDB is authoritative; LanceDB can be rebuilt
from MariaDB at any time).

**huf's approach:** 9 pluggable backends via a `KnowledgeBackend` ABC.

**What frappe_ai should adopt:**

**A. Pluggable backend interface.** LanceDB is a good default but operators who
already run pgvector (common on Frappe Cloud Postgres instances) or Redis should not
be forced to add LanceDB. huf's `KnowledgeBackend` ABC is the right abstraction:

```python
class KnowledgeBackend(ABC):
    def initialize(self, source, config) -> None: ...
    def add_chunks(self, chunks) -> None: ...
    def delete_chunks(self, input_id: str) -> None: ...
    def search(self, query: str, top_k: int, filters: dict) -> list[ChunkResult]: ...
    def clear(self) -> None: ...
```

LanceDB remains the default (`knowledge_type = "lancedb"`). A `pgvector` backend
using the same `AI Knowledge Source` DocType config fields (`pgvector_connection_*`)
is the first additional backend to add, given Frappe Cloud's Postgres availability.

**B. Embedding cache.** ADR 0018 Finding 5 already flags this: `embed_texts([query])`
is called on every retrieval with no cache. huf has the same gap. Both should add a
read-through cache keyed on `sha256(query_text)` → `list[float]`, invalidated never
(embeddings are deterministic for a given model — the only invalidation case is a
model change, which changes the model ID and therefore the key).

**ADR 0017 invariant is preserved:** MariaDB (`AI Knowledge Chunk`) remains
authoritative for chunk text regardless of which vector backend stores the vectors.
LanceDB being disposable does not require LanceDB to be the only option.

---ls

### ADR 0003 — Tools Execute Inside Frappe

**frappe_ai's position:** Every Frappe-data tool call HTTP-callbacks from FastAPI
back into Frappe, where `frappe.set_user(acting_user)` is called before execution.
This is the load-bearing security invariant of the whole system.

**huf's approach:** Tools execute in the RQ worker that runs the LLM loop. There is
no HTTP callback — the tool function runs in-process with `frappe.set_user`
implicit from the worker's request context.

**Assessment for frappe_ai:** frappe_ai's approach is more explicit (the user
identity is set at every dispatch boundary, not assumed from ambient context) and
more auditable. **No change.**

**One thing to adopt from huf:** huf's `Agent Tool Function` DocType has a
`requires_confirmation` field. frappe_ai's `AI Tool` has the same. But huf also
surfaces which tool call is pending directly in the `Agent Run` payload, with the
arguments as structured JSON. frappe_ai's `AI Run.questions` (JSON array) already
does this. The gap is in the **resume path**: huf's `resume_run` is not yet
implemented cleanly (it is the subject of `docs/to_do/high-chatpy-confirmation-
extraction.md`). This is a shared gap, not something frappe_ai adopts from huf.

---

### ADR 0004 — SSE Direct from FastAPI

**frappe_ai's position:** The browser connects directly to FastAPI for the SSE
stream, bypassing Frappe's gunicorn entirely. Frappe workers are only called at
three points: `start_run`, `dispatch_tool`, `persist_run_result`.

**huf's approach:** SSE goes through Frappe's `agent_stream_renderer`, which holds a
Frappe worker for the duration of each streaming response.

**Assessment for frappe_ai:** frappe_ai is right here. Under load, huf's design
means a single busy agent can consume all gunicorn workers if multiple users stream
simultaneously. frappe_ai's design is strictly better for production. **No change.**

---

### ADR 0007 — Failure Over Durable Execution

**frappe_ai's position:** Chose not to build durable graph-based execution (flows,
procedures). If a run fails mid-way, it fails completely and the user retries. The
rationale: durable execution requires distributed state, versioned graph definitions,
pause/resume infrastructure, and idempotent tool calls — complexity that is not
justified by frappe_ai's target use case of "assist an ERP user in the desk."

**huf's approach:** Full `GraphExecutor` with 12 Flow node types, 7 Procedure node
types, content-addressed versioning, parallel execution, write idempotency.

**What frappe_ai should partially adopt:**

**Lightweight multi-step sequences** — not the full graph executor, but a mechanism
to define and reuse a deterministic sequence of tool calls without an LLM in the
path. The use case: "run Month-End Closing" → a fixed sequence of `read` →
`run_action` → `run_action` → `send_email` steps that always execute in the same
order with the same tool arguments, no routing logic needed.

This does not require building huf's `GraphExecutor`. It requires:
1. A `Playbook` DocType — an ordered list of steps, each a tool name + arguments template
2. A `run_playbook(playbook_name, context)` whitelisted method that executes steps
   sequentially, with the same budget enforcement as `dispatch_tool`
3. Steps can reference prior step outputs via simple `{{ step_1.result.name }}`
   Jinja2 references

This is 5% of huf's flow complexity and covers 80% of the "repeatable ERP workflow"
use case frappe_ai's triggers currently serve with an LLM in the path.

---

### ADR 0008 — Execution Budgets

**frappe_ai's position:** Per-run budgets (`max_tool_calls`, `max_mutations`,
`max_records_per_call`, `max_runtime_seconds`) enforced with `SELECT … FOR UPDATE`
on the `AI Run` row. Budget state lives in `AI Run.budget_usage` JSON.

**huf's approach:** `ExecutionProfile` covers infrastructure resources (wall-time,
memory, CPU, output bytes). No LLM-level mutation budgets with DB locking.

**Assessment for frappe_ai:** frappe_ai is right here. huf should adopt this, not
the other way around. The `SELECT … FOR UPDATE` pattern specifically prevents
parallel tool calls from both seeing "within budget" and both proceeding — a real
risk when Agno issues tool calls in parallel.

**One known gap in frappe_ai (from `to_do/high-mcp-budget-bypass.md`):** MCP tool
calls bypass budget enforcement entirely. huf's tool invocation path has the same
gap for MCP tools. Both apps need **Option B** from the to_do: a service-side
counter reported back through `persist_run_result`.

---

### ADR 0009 + ADR 0013 + ADR 0014 — Provider Architecture

**frappe_ai's position (ADR 0014):** Uses OpenAI-compatible chat transport only.
Agno provides model clients for OpenAI, Anthropic, Google, Groq, etc. natively via
their own SDKs. ADR 0009 says "no LiteLLM at execution time" — LiteLLM adds a
translation layer that can change tool-call wire format in ways that break Agno's
parsing.

**huf's approach:** LiteLLM is the execution-level router. All providers go through
it. Anthropic, Google, and OpenAI are also available as direct implementations.

**What this means for frappe_ai:**

frappe_ai cannot currently use:
- **Anthropic prompt caching** (requires `cache_control` in the Anthropic API format,
  not expressible through OpenAI-compatible transport)
- **Google Gemini native function calling** (Gemini's function-declaration format
  differs from OpenAI's; OpenAI-compatible wrappers lose fidelity)
- **Providers without an OpenAI-compatible API** (some local/private LLM deployments)
- **LiteLLM cost tracking** (per-call cost estimation, not available without LiteLLM
  as the execution router)

**What frappe_ai should adopt:**

ADR 0009's concern (LiteLLM changing tool-call wire format) was valid at the time
it was written. LiteLLM has since stabilized its OpenAI-format passthrough. The
specific risk — "LiteLLM 1.82.7/1.82.8 supply-chain compromise" — is a separate
issue (blocked in huf's pyproject.toml). The format-fidelity concern is mitigable
by using LiteLLM only for non-OpenAI providers (Anthropic, Google, Groq, Ollama)
while keeping the direct OpenAI path for OpenAI/OpenAI-compatible endpoints.

**Concrete recommendation:** Add LiteLLM as an optional execution router. When
`AI Provider.provider_type = "litellm"`, route through LiteLLM. When
`provider_type = "openai_compatible"`, use the current direct OpenAI SDK path.
This unlocks prompt caching and cost tracking for Anthropic/Google users without
changing anything for existing OpenAI deployments.

**ADR update required:** ADR 0009 and ADR 0014 should be marked "superseded in
part" with a note that LiteLLM is now supported as an optional execution router
for non-OpenAI-compatible providers.

---

### ADR 0018 Finding 3 — MCP Session Reuse

**frappe_ai's position:** `_build_mcp_tools` creates a fresh `MCPTools(...)` per
chat turn without calling `.connect()`. Per Agno's docs, this triggers "automatic
connection and closure on each run, which may impact performance."

**huf's approach:** `mcp_client.py` maintains a `mcp_session_pool()` as an async
context manager using `contextvars.ContextVar`. Sessions are created on first use,
reused across turns within the same agent run, and torn down when the run ends.

**What frappe_ai should adopt:** huf's session pool pattern directly. Implement a
`_MCPSessionPool` in `service/builder.py` or a new `service/mcp_pool.py`:

```python
_mcp_pools: ContextVar[dict[str, MCPTools]] = ContextVar("mcp_pools", default={})

@asynccontextmanager
async def mcp_session_pool():
    pools = {}
    token = _mcp_pools.set(pools)
    try:
        yield pools
    finally:
        for pool in pools.values():
            await pool.close()
        _mcp_pools.reset(token)

def get_or_create_mcp_session(connection_name: str, build_fn) -> MCPTools:
    pools = _mcp_pools.get()
    if connection_name not in pools:
        tools = build_fn()
        await tools.connect()
        pools[connection_name] = tools
    return pools[connection_name]
```

`stream_chat` wraps the entire run in `async with mcp_session_pool()`. The
per-turn `AgentBuilder.build()` calls `get_or_create_mcp_session` instead of
building a new `MCPTools`. Each MCP server connects once per run, not once per
turn. Sessions close when `stream_chat` exits (success or error).

This is a pure performance improvement with no semantic change. Already flagged
in ADR 0018.

---

### ADR 0018 Finding 6 — Multi-Agent / Team Pattern (Dangling field)

**frappe_ai's position:** `AI Agent.agent_type` Select field with options
`Agent / Team` exists but is never read. `AgentBuilder.build()` unconditionally
returns an Agno `Agent`, never an Agno `Team`.

**huf's approach:** `Agent Orchestration` — a sequential multi-step plan where each
step is a separate LLM call with a scratchpad-enriched prompt. Not graph-based, but
practical for delegating subtasks.

**What frappe_ai should adopt:**

**Phase 1 (short-term):** Remove the dangling `agent_type` field entirely, or
document it as "not yet wired" in `003-doctype-reference.md`. A field that silently
does nothing is worse than no field.

**Phase 2 (medium-term):** Implement Agno `Team` for multi-agent delegation. The
ERP use case is clear: a **Coordinator** agent delegates to domain specialists:

```
User: "Prepare month-end report for Accounts and check if Payroll is ready to run"

Coordinator Agent
  ├─ → Accounts Agent   (reads GL entries, outstanding invoices)
  └─ → HR/Payroll Agent (checks payroll checklist status)
```

Agno's `Team` with `route` mode is the right primitive for this. The `AI Agent`
DocType needs:
- `team_members: Table → AI Agent Team Member` (child table listing member agents
  by priority)
- `team_strategy: Select (route / coordinate / collaborate)`

The `AgentBuilder.build()` path for `agent_type = "Team"` builds `agno.team.Team`
with member `Agent` instances. Budget enforcement applies to the team run as a whole
(the team's `AI Run` row), not per-member.

---

### Known Gap: Wildcard `doc_events` (both apps)

**frappe_ai's position:** `"*"` hooks on 5 events. Every save of every DocType on
the site calls `frappe_ai.triggers.dispatch`. The to_do document
(`to_do/medium-wildcard-doc-events.md`) already describes the fix: dynamically
resolve triggered DocTypes and register only those.

**huf's position:** Same `"*"` hooks on 14 events. Same performance problem.

**What frappe_ai should implement (already in to_do):**

```python
# hooks.py — generated dynamically, not hardcoded
def get_doc_events():
    """Build doc_events from enabled AI Trigger rows at hook-load time."""
    triggered_doctypes = frappe.get_all(
        "AI Trigger", 
        filters={"enabled": 1, "event": ["like", "DocType%"]},
        pluck="target_doctype",
        distinct=True
    )
    return {
        dt: {event: "frappe_ai.triggers.dispatch" for event in TRIGGER_EVENTS}
        for dt in triggered_doctypes
    }

doc_events = get_doc_events()  # evaluated at hook load time
```

Cache invalidation: `AI Trigger.on_update` and `AI Trigger.on_trash` call
`frappe.clear_cache()` to force hook reload on next request. Tests: saving an
untriggered DocType performs zero frappe_ai queries.

---

## New Features frappe_ai Should Add (from huf)

These are capabilities huf has that frappe_ai currently lacks entirely, ordered
by value-to-effort ratio for frappe_ai's use case.

---

### 1. Multi-Channel Gateway Layer [HIGH VALUE / MEDIUM EFFORT]

**What huf has:** A `GatewayAdapter` ABC with 8 channel implementations
(WhatsApp, Telegram, Slack, Discord, Email, Teams, Google Chat, Messenger). A
`Gateway` DocType with routing rules (`Gateway Binding`) and access policies.

**Why frappe_ai needs it:**

frappe_ai's agents currently only operate from the Frappe desk. A WhatsApp or
Telegram user who wants to query live ERP data ("What is the stock of Item X?",
"Is my PO #1234 approved?", "Create a leave request for tomorrow") cannot reach a
frappe_ai agent.

Adding a gateway layer transforms frappe_ai from a desk-only assistant to a
multi-surface ERP interface — without building a separate mobile app or portal.

**Minimal implementation for frappe_ai:**

Adopt huf's `GatewayAdapter` ABC verbatim. Start with two adapters:
1. **Telegram** (easiest — webhook registration via `setWebhook`, no approval
   process) — covers internal business users
2. **Email** (already partially handled via Frappe's `Communication` doc event) —
   covers supplier/customer queries

Add three DocTypes (import from huf's schema):
- `AI Gateway` — provider, enabled, integration credentials, default agent,
  DM policy (`Open / Allow List / Pairing`)
- `AI Gateway Binding` — priority-ordered routing rules (sender → agent)
- `AI Gateway Access Entry` — allow-list entries for `Allow List` policy

The `handle_gateway_webhook` endpoint and `GatewayAdapterRegistry` can be ported
directly from huf.

---

### 2. Structured Reasoning Controls [HIGH VALUE / LOW EFFORT]

**What huf has:** `reasoning_mode (Auto/Off/On)`, `reasoning_effort (Auto/Low/Medium/High)`,
`reasoning_budget_tokens (Int)`, `reasoning_summary (None/Concise/Detailed)` on the
`Agent` DocType.

**What frappe_ai has:** `reasoning: bool` — a single on/off toggle. Passed directly
to `Agent(reasoning=...)` in `AgentBuilder`.

**Why frappe_ai needs it:**

Anthropic's extended thinking and OpenAI's o1/o3 models have structured reasoning
controls that a `bool` cannot express. With `reasoning=True`, an agent using
`claude-3-5-sonnet` gets a default reasoning budget; but with `claude-opus-4` or
`o3`, you want `reasoning_effort="high"` for complex accounting reconciliation and
`reasoning_effort="low"` for simple document lookups.

**Implementation:**

Add three fields to `AI Agent`:
- `reasoning_effort: Select (auto / low / medium / high)` — default `auto`
- `reasoning_budget_tokens: Int` — 0 means "use provider default"

Update `AgentBuilder`:
```python
reasoning_kwargs = {}
if cfg.get("reasoning"):
    effort = cfg.get("reasoning_effort", "auto")
    if effort != "auto":
        reasoning_kwargs["reasoning_effort"] = effort
    budget = cfg.get("reasoning_budget_tokens", 0)
    if budget:
        reasoning_kwargs["reasoning_budget_tokens"] = budget

Agent(..., reasoning=cfg["reasoning"], **reasoning_kwargs)
```

---

### 3. Run Analytics [MEDIUM VALUE / LOW EFFORT]

**What huf has:** `Agent Run Analytics Rollup` DocType, `refresh_rollups` scheduled
job (every 5 minutes), analytics dashboard page in the SPA with token cost
breakdowns per agent, per model, per day; tool call distribution; success/failure
rates.

**What frappe_ai has:** `AI Run.usage` (JSON with `input_tokens`, `output_tokens`,
`total_tokens` per run) and `feedback_rating`.

**Why frappe_ai needs it:**

Token costs are invisible to frappe_ai operators today. A trigger agent firing on
every Sales Order submit could be burning thousands of tokens daily without anyone
noticing until the API invoice arrives.

**Implementation:**

Add a `frappe_ai.analytics` module:

```python
# Runs every 5 minutes via scheduler
def refresh_run_analytics():
    """Aggregate AI Run usage into daily rollup rows."""
    rows = frappe.db.sql("""
        SELECT 
            agent,
            DATE(creation) as date,
            COUNT(*) as runs,
            SUM(JSON_EXTRACT(usage, '$.input_tokens')) as input_tokens,
            SUM(JSON_EXTRACT(usage, '$.output_tokens')) as output_tokens,
            SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN status='Failed' THEN 1 ELSE 0 END) as failed
        FROM `tabAI Run`
        WHERE creation >= %s
        GROUP BY agent, DATE(creation)
    """, [last_refresh], as_dict=True)
    # Upsert into AI Run Analytics Rollup
```

Expose via a Frappe Report or a new `AI Analytics` page in the desk panel inspector.

---

### 4. Skills — Reusable Capability Packs [MEDIUM VALUE / MEDIUM EFFORT]

**What huf has:** `Skill` DocType — a named bundle of tools + knowledge sources +
prompts + MCP servers. Agents attach skills; the skill's components are merged into
the agent's active capabilities at run time.

**Why frappe_ai needs it:**

A "Procurement Officer" skill bundles the Purchase Order knowledge base + supplier
analysis prompts + the `read`/`create`/`run_action` tools pre-filtered to purchase
DocTypes + an MCP connection to the vendor catalog. Attaching this skill to any
agent instantly makes it a procurement specialist without duplicating the config.

Currently, every frappe_ai agent that needs procurement knowledge must individually
configure every knowledge base, tool, and MCP connection — there's no reuse unit
smaller than "copy the whole agent."

**Minimal DocType:**
```
AI Agent Skill (Master)
  title           Data
  description     Small Text
  tools           Table → AI Skill Tool (tool Link)
  knowledge_bases Table → AI Skill KB (knowledge_base Link)
  mcp_connections Table → AI Skill MCP (mcp_connection Link)
  instructions    Long Text  (injected into system prompt as a section)
  enabled         Check

AI Agent Skill Binding (child of AI Agent)
  skill           Link → AI Agent Skill
  enabled         Check
```

`AgentBuilder` merges the skill's tools, KBs, and MCP connections into the agent's
own lists before building the Agno `Agent`. The `instructions` field is appended to
the system prompt under a `## Skills` section.

---

### 5. Feedback → Memory Pipeline [MEDIUM VALUE / LOW EFFORT]

**What huf has:** `Agent Run Feedback` DocType. No pipeline to memory yet (marked
as a P2 improvement in `FRAPPE_AI_COMPARISON.md`).

**What frappe_ai has:** Thumbs-up/down + comment on `AI Run.feedback_*` fields.
No pipeline to memory.

**What to implement in frappe_ai:**

In `api/api.py`, in `submit_feedback()`, after writing `feedback_rating` and
`feedback_comment`:

```python
if rating == "thumbs_down" and comment:
    from frappe_ai.memory.memory import save_feedback_memory
    save_feedback_memory(
        agent=run_doc.agent,
        user=frappe.session.user,
        source_run=run_name,
        correction=comment
    )
```

`save_feedback_memory` writes an `AI Agent Memory` row with `scope="Agent"`,
`source="Feedback"`, `content=f"User corrected the agent: {comment}"`, and
`keywords="correction,feedback"`.

On subsequent runs, this correction appears in the `<agent_memory>` block, teaching
the agent from past mistakes automatically.

---

### 6. Content-Hash Deduplication in Knowledge (already in frappe_ai ✓)

frappe_ai already implements `content_hash` per chunk in `knowledge/ingest.py`.
huf lacks this. **No action for frappe_ai** — document this as an advantage.

---

### 7. Pluggable Embedding Backends [MEDIUM VALUE / MEDIUM EFFORT]

**What huf has:** `embedding.py` calls `litellm.embedding(model, texts)` — any
LiteLLM-supported embedding model. frappe_ai's `embedder.py` uses the provider's
own SDK directly (ADR 0012).

ADR 0012 rationale: "Agno's embedding utilities wrap providers inconsistently;
direct SDK calls give exact control over batching and error handling."

**What frappe_ai should adopt:**

Not LiteLLM for embeddings (ADR 0012 stands), but **embedding model per knowledge
source** rather than a single global embedding model in `AI Settings`.

Currently, every knowledge base on a site uses the same embedding model configured
in `AI Settings.embedding_dimension`. This means:
- You cannot have a text knowledge base (1536-dimension, ada-002) and an image
  knowledge base (different dimension) coexist
- Switching the global embedding model requires rebuilding all knowledge bases

huf's `Knowledge Source` DocType has per-source `embedding_model` and
`vector_dimension` fields. frappe_ai should add these to `AI Knowledge Source` and
use them in `ingest.py` / `retriever.py` instead of always reading from `AI Settings`.

---

### 8. Memory Injection Mode [LOW VALUE / LOW EFFORT]

**What huf has:** `Memory Policy.inject_mode = Never / Relevant Only / Always /
Tool Only`. Agents can choose not to auto-inject memories (useful for agents where
memory would clutter the prompt) while still allowing the agent to explicitly search
memories via a tool call.

**What frappe_ai has:** Always injects active memories (up to 20, relevance-selected).
No per-agent control.

**What to implement:**

Add `memory_injection_mode: Select (always / relevant_only / tool_only / never)` to
`AI Agent`. Default: `relevant_only`.

In `memory/memory.py`, `build_memory_block()`:
- `never` → return empty string immediately
- `always` → current behavior (inject all up to cap)
- `relevant_only` → current behavior (inject relevance-selected subset)
- `tool_only` → return empty string but keep the `update_memory` tool wired

---

## Summary — Priority Matrix for frappe_ai

| # | From huf | Impact | Effort | Priority |
|---|---|---|---|---|
| 1 | **LiteLLM as optional execution router** (for Anthropic/Google/Ollama users) | High | Medium | P0 |
| 2 | **MCP session pool / connection reuse** per run (ADR 0018 Finding 3) | High | Low | P0 |
| 3 | **Multi-channel gateway layer** (Telegram + Email first) | High | High | P0 |
| 4 | **Fix wildcard doc_events** — dynamic DocType registration | Medium | Medium | P1 |
| 5 | **Pluggable knowledge backends** — pgvector first (LanceDB default kept) | Medium | Medium | P1 |
| 6 | **Structured reasoning controls** (`reasoning_effort`, `reasoning_budget_tokens`) | Medium | Low | P1 |
| 7 | **MCP budget enforcement** — service-side counter via `persist_run_result` (Option B) | High | Medium | P1 |
| 8 | **Lightweight Playbook** — deterministic multi-step sequences without LLM | Medium | Medium | P2 |
| 9 | **Multi-agent Team** — Agno `Team` for coordinator→specialist delegation | Medium | High | P2 |
| 10 | **Run analytics rollup** — token cost / call volume per agent per day | Medium | Low | P2 |
| 11 | **Skills** — reusable tool + knowledge + MCP bundles | Medium | Medium | P2 |
| 12 | **Feedback → memory pipeline** | Low | Low | P2 |
| 13 | **Per-knowledge-source embedding model** | Low | Low | P3 |
| 14 | **Memory injection mode** | Low | Low | P3 |
| 15 | **Embedding cache** (`sha256(query)` → vector) | Medium | Low | P3 |

---

## What NOT to Adopt from huf

These huf designs are correct for huf's broader scope but would be wrong for
frappe_ai's focused ERP-assistant scope.

| huf feature | Why frappe_ai should not adopt |
|---|---|
| **Full GraphExecutor + Flow builder** | ADR 0007 stands. Durable graph execution is operationally complex; frappe_ai's trigger + single-run model is simpler and sufficient. Lightweight Playbooks cover 80% of the use case without the complexity. |
| **9-backend knowledge system as a primary** | LanceDB + pgvector as a second backend covers the real operator needs. Supporting 9 backends adds maintenance burden without proportional value. |
| **huf's `Memory Policy` + `Memory Record` system** | frappe_ai's simpler `AI Agent Memory` with an `inject_mode` flag is sufficient. huf's TTL, scope hierarchy, and vector retrieval are over-engineered for the ERP assistant context. |
| **RQ worker for LLM calls** | frappe_ai's FastAPI sidecar is architecturally superior for streaming. Moving LLM calls into RQ workers would regress SSE performance. |
| **huf's 100+ provider LiteLLM routing** | frappe_ai uses Agno's native provider clients, which have tighter tool-call format guarantees (ADR 0009). LiteLLM should be added only as an opt-in router for non-OpenAI-compatible providers, not as the default for all. |
| **Batch API support** | The batch use case (100 Sales Orders fire the same trigger) is better solved by queuing and rate-limiting individual runs, not by batching LLM calls. Batch adds complexity for an async pattern frappe_ai's synchronous trigger model doesn't easily compose with. |
| **Voice and Meetings** | Out of scope for an ERP desk assistant. Worth revisiting if frappe_ai's scope expands to field workers or mobile use. |

---

## Open Items from frappe_ai's Own to_do That huf Has Already Solved

These are gaps in frappe_ai's to_do list where huf's implementation can be
referenced as a solution pattern:

| frappe_ai to_do | huf's solution | Reference |
|---|---|---|
| `high-mcp-budget-bypass.md` — MCP calls bypass budget counters | Service-side counter, per-run session pool scopes the count | `huf/ai/mcp_client.py:mcp_session_pool` |
| `medium-wildcard-doc-events.md` — wildcard hooks on every write | Same gap — huf hasn't solved it either. Both apps need the dynamic registration fix. | — |
| `high-chatpy-confirmation-extraction.md` — confirmation logic in a 860-line file | huf's confirmation is cleanly split: `agent_execution_approval.py` DocType + `execution_api.py` whitelisted method + frontend `ConfirmCard` component. The logic separation is a model to follow. | `huf/huf/doctype/agent_execution_approval/`, `huf/ai/execution_api.py` |
| ADR 0018 Finding 3 — MCP reconnects every turn | `mcp_session_pool()` with `ContextVar` per-run reuse | `huf/ai/mcp_client.py:mcp_session_pool` |
| ADR 0018 Finding 5 — embedding re-computed every query | Shared gap — huf also lacks embedding cache. Both should add `sha256(query)` → vector cache. | — |
| ADR 0018 Finding 6 — dangling `agent_type = Team` field | huf's `Agent Orchestration` as a reference for sequential multi-agent coordination | `huf/ai/orchestration/orchestrator.py` |
