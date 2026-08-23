# frappe_ai Documentation

AI agent capabilities for Frappe — conversational agents with tools, retrieval-augmented
knowledge, event-driven automation, persistent memory, and a full audit trail.

**Architecture:** Frappe (config, persistence, permissions) + FastAPI (async orchestration)
+ Agno (agent framework) + LanceDB (vectors).

**Current status:** ✅ Phase 5 verified (Triggers, Memory & MCP). Phase 6 frontend work
now has an API-first React SPA with a dedicated `/app/frappe-ai` workspace, plus thin
Desk/page host adapters for compatibility. The same-origin frontend contract is now
documented explicitly for custom clients. See the
[progress tracker](progress/flow-to-frappe-ai-migration.md).

> ⚠️ **Phases 1–7 deliver parity, not production readiness.** Safety-critical items —
> SSE heartbeats, execution budgets, mutation limits, rate limiting — are scheduled for
> **Phase 8.1**, which is a hard gate before any production traffic. Details in the
> [progress tracker](progress/flow-to-frappe-ai-migration.md#parity--production-ready).

---

## Start Here

| If you want to… | Read |
|---|---|
| Understand how the system fits together | [001 — Architecture](specifications/001-architecture.md) |
| Know what feature lives where | [002 — Feature Mapping](specifications/002-feature-mapping.md) |
| Look up a DocType or field | [003 — DocType Reference](specifications/003-doctype-reference.md) |
| Implement or review a custom frontend client | [005 — Frontend Contract](specifications/005-frontend-contract.md) |
| Know where the work stands | [Progress tracker](progress/flow-to-frappe-ai-migration.md) |
| Understand *why* something is the way it is | [Decisions](#decisions) below |
| See what surprised us while building this, and why | [Learnings](learnings.md) |

---

## Specifications

| Doc | Contents |
|---|---|
| [001 — Architecture](specifications/001-architecture.md) | Component boundaries, request lifecycles, auth model, data architecture, streaming protocol, failure handling, deployment |
| [002 — Feature Mapping](specifications/002-feature-mapping.md) | Every `flow` capability and its `frappe_ai` equivalent, marked Port / Adapt / Redesign / New / Drop. The parity checklist. |
| [003 — DocType Reference](specifications/003-doctype-reference.md) | All 18 DocTypes: fields, types, naming rules, controllers, permissions |
| [006 — Dynamic MCP Server Profiles](specifications/006-dynamic-mcp-server-profiles.md) | Central AI Tool definitions, configurable MCP profiles, dynamic tool publication, and migration from app-specific MCP server files |
| [005 — Frontend Contract](specifications/005-frontend-contract.md) | Stable same-origin JSON endpoints, SSE stream protocol, host adapter boundaries, and end-to-end client flows for the standalone SPA or any custom frontend |

## Decisions

| ADR | Decision | Why it matters |
|---|---|---|
| [0001](decisions/0001-agno-fastapi-over-frappe-native.md) | Agno + FastAPI instead of a Frappe-native runtime | The reason this project exists — `flow` blocks a worker per run |
| [0002](decisions/0002-lancedb-vector-store.md) | LanceDB as the vector store | Preserves hybrid search and BM25 memory recall; ChromaDB rejected |
| [0003](decisions/0003-tools-execute-in-frappe.md) | Tools execute inside Frappe, as the acting user | **The security decision.** Keeps per-user permissions intact across the process split |
| [0004](decisions/0004-sse-direct-from-fastapi.md) | Stream SSE directly from FastAPI | Without this, Frappe workers stay blocked and ADR 0001 buys nothing |
| [0005](decisions/0005-greenfield-no-migration.md) | Greenfield; no data migration from `flow` | Sets scope — `flow` is a spec, not a source of data |
| [0006](decisions/0006-unified-safe-exec-namespace.md) | One hardened `safe_exec` namespace | Fixes a permission-bypass present in `flow`; explains why Agno doesn't replace sandboxing |
| [0007](decisions/0007-failure-over-durable-execution.md) | Fail-and-retry, not mid-run resume | Why a service restart fails runs cleanly instead of resuming them; triggers stay durable via RQ |
| [0008](decisions/0008-execution-budgets.md) | Execution budgets and mutation limits | Bounds *how much* an agent can do, where ADR 0003 bounds *what* it can touch |
| [0009](decisions/0009-no-litellm-agno-native-models.md) | Drop litellm; use Agno's native per-provider model classes | Removes a redundant abstraction layer under Agno — `AI Model` maps directly onto an Agno model class |
| [0010](decisions/0010-service-bootstrap-via-env-vars.md) | ~~FastAPI service bootstraps via environment variables~~ — **Superseded by 0011** | Env-var secret required a manual export step `bench start` didn't automate; caused a real boot failure |
| [0011](decisions/0011-service-secret-in-site-config.md) | Service secret lives in `site_config.json`, not a DB field + env var | `bench start` boots the service unattended; one source of truth instead of two kept in sync by hand |
| [0012](decisions/0012-embeddings-direct-provider-sdk.md) | Embeddings via direct provider SDK calls, not litellm or Agno | No `agno.embedder` exists; extends ADR 0009's reasoning to the one call type it didn't originally cover |
| [0013](decisions/0013-litellm-for-provider-ux-agno-still-executes.md) | ~~litellm for provider/model UX only; Agno still executes chat~~ — **Superseded by 0014** | Historical provider/model UX decision; LiteLLM remains UX-only |
| [0014](decisions/0014-openai-compatible-chat-transport.md) | One OpenAI-compatible transport for all chat execution | Removes provider SDK coupling while preserving Agno orchestration, tools, confirmations, structured output, and streaming |

---

## The Four Load-Bearing Rules

Everything else follows from these. When an implementation question is ambiguous, resolve
it against them.

### 1. Frappe authorizes; FastAPI orchestrates

> The FastAPI service never reads or writes the Frappe database directly, and never holds a
> credential at rest.

Every Frappe-touching tool call is dispatched back to Frappe carrying the originating
user's identity, so `frappe.has_permission` still governs. A compromised service cannot
exceed the permissions of the user it is acting for. → [ADR 0003](decisions/0003-tools-execute-in-frappe.md)

### 2. MariaDB is authoritative; LanceDB is disposable

Chunk text and metadata live in `AI Knowledge Chunk`. LanceDB holds only vectors and FTS
indexes, keyed by chunk name, and can be rebuilt from MariaDB at any time.

`AI Knowledge Chunk` uses `autoincrement` naming because its integer name **is** the
LanceDB row `id`. Changing that naming rule silently breaks retrieval.
→ [ADR 0002](decisions/0002-lancedb-vector-store.md)

### 3. All sandboxed code uses one hardened namespace

`execute`, Script `AI Tool` rows, and `AI Trigger.condition` all run through
`frappe_ai/utils/safe_exec.py`, which excludes `frappe.db.sql`, `frappe.qb`,
`frappe.db.set_value`, and `frappe.get_all`.

Agno validates tool *interfaces*; it does not sandbox tool *implementations*. It is not a
substitute. → [ADR 0006](decisions/0006-unified-safe-exec-namespace.md)

### 4. Permissions bound *what*; budgets bound *how much*

Rule 1 guarantees an agent cannot touch data the user could not touch. It does **not** bound
how many records a legitimately-permitted agent writes — and `auto_approve` triggers have no
human check at all.

Per-run budgets (`max_tool_calls`, `max_mutations`, `max_records_per_call`,
`max_runtime_seconds`) close that gap, enforced at dispatch **and** inside each mutating
builtin. → [ADR 0008](decisions/0008-execution-budgets.md)

---

## Relationship to `apps/flow`

`apps/flow` is a working, Frappe-native AI agent framework and is the **functional
specification** for this app. It is not a dependency, and no data migrates from it.

Once `frappe_ai` reaches parity, `flow` is uninstalled — after the pre-uninstall checklist
in [ADR 0005](decisions/0005-greenfield-no-migration.md).

Two things from `flow` are deliberately **not** carried forward:

1. Its `safe_exec` asymmetry, where Script tools got a broader sandbox than the tool
   documented as sandboxed ([ADR 0006](decisions/0006-unified-safe-exec-namespace.md)).
2. Its `stream_with_persistence` commit choreography — a WSGI workaround with no FastAPI
   analogue.

---

## Documentation Conventions

Established here; follow them for anything added later.

```
docs/
├── README.md                  # this index
├── specifications/            # NNN-topic.md — what the system does
├── decisions/                 # NNNN-slug.md — why it does it that way
├── progress/                  # feature-name.md — where the work stands
└── learnings.md                # what surprised us, and why — a running log
```

**Specifications** are numbered `001`, `002`, … and describe current intended behaviour.
When behaviour changes, edit the spec — do not append a changelog to it.

**Decisions** are numbered `0001`, `0002`, … and are immutable once Accepted. To change a
decision, write a new ADR and mark the old one `Superseded by NNNN`. Each records Context,
Decision, Consequences (positive **and** negative), Alternatives Considered, and
Verification.

**Progress** files are living documents, updated continuously during implementation rather
than written once at the start or backfilled at the end.

**Learnings** is a single running log (newest first) of things that surprised us while
building this — an assumption that turned out wrong, an API that behaved differently than
documented, a wall hit and worked around. Unlike an ADR it isn't a decision record: it's
the "why we know what we know" trail. When a learning leads to an actual fix, promote the
fix into the relevant spec/controller and leave the learnings entry as the historical
record of how it was found.

Per the repository's `CLAUDE.md`, this documentation lives in the app whose code it
describes, and travels with the repo.
