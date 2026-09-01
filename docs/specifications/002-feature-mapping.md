# 002 — Feature Mapping: `flow` → `frappe_ai`

**Status:** Approved. Sections 1–8 are implemented through Phase 5; Sections 9–11 remain
the parity/reconciliation checklist for later phases.
**Purpose:** Exhaustive inventory of every `flow` capability and its `frappe_ai`
equivalent. This is the parity checklist — a feature is not migrated until its row here is
marked done in the [progress tracker](../progress/flow-to-frappe-ai-migration.md).

---

## Legend

| Mark | Meaning |
|---|---|
| **Port** | Logic copied with mechanical renaming; behaviour identical |
| **Adapt** | Same capability, different implementation for the new runtime |
| **Redesign** | Capability preserved but materially reworked; behaviour differs |
| **New** | No `flow` equivalent |
| **Drop** | Deliberately not carried forward |

---

## 1. Configuration Tier

> **litellm is UX-only.** `flow`'s configuration tier validates providers and model
> ids against litellm, and auto-detects context window via
> `litellm.get_model_info()`. `frappe_ai` uses litellm only for provider/model
> suggestions; provider validation and model instantiation use the shared
> OpenAI-compatible transport. Rows 1.1, 1.2, 1.5, 1.6,
> and 1.8 below are marked **Redesign** as a result. See [ADR 0014](../decisions/0014-openai-compatible-chat-transport.md)
> and [ADR 0015](../decisions/0015-configuration-time-model-capability-tests.md).

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 1.1 | `Flow Provider` — per-provider credential store, `field:provider` naming lowercased to match litellm | `AI Provider` | **Redesign** | `api_key` stays a Password field, never leaves Frappe. `provider` is lowercased and validated against Agno-supported provider slugs, not `litellm.provider_list`. |
| 1.2 | Provider validation against `litellm.provider_list` | Validated against a fixed set of Agno provider slugs, each mapped to an `agno.models.<slug>` import path | **Redesign** | ADR 0009. |
| 1.3 | `extra_params` JSON merged under model params | same | **Port** | Model-level values always win. |
| 1.4 | `Flow Model` — model config, `field:title` naming | `AI Model` | **Port** | Field shape unchanged; validation logic changes per 1.5/1.6/1.8. |
| 1.5 | `_apply_provider` prefixing `provider/` onto `model_id` | Dropped. `model_id` is a bare id (e.g. `"claude-sonnet-4-6"`); provider comes from the linked `AI Provider`, not string composition. | **Redesign** | ADR 0009. |
| 1.6 | `_resolve_context_window` via `litellm.get_model_info` | Dropped. `context_window` is a plain user-editable `Int`, not derived. | **Redesign** | No Agno equivalent exists; documented as a deliberate loss of convenience, not a functional regression. ADR 0009. |
| 1.7 | `RESERVED_PARAM_KEYS` rejection | same | **Port** | |
| 1.8 | `test_connection()` — 1-token ping via `litellm.completion` | Explicit fresh Chat capability suite; runtime never preflights | **Redesign** | Core checks cover non-streaming, streaming, and synthetic tool declaration/call round trip. Structured output and bounded larger input are warnings. See ADR 0015. |
| 1.9 | `get_provider_models(provider)` | same, sourced from the fixed Agno provider-slug set rather than `litellm.models_by_provider` | **Adapt** | |
| 1.10 | `Flow Knowledge Settings` (Single) — embedding model, chunk size/overlap, search type | `AI Settings` (Single) | **Adapt** | Embedding selection removed; fixed Ollama `nomic-embed-text` plus observed dimension are application configuration. Other fields remain, alongside `service_base_url`, timeouts, and `lancedb_path`. See ADR 0016. |
| 1.11 | `_guard_model_change` — blocks embedding-model change while chunks exist | Fixed model identity and dimension checks in the embedder/LanceDB metadata | **Redesign** | Model selection is impossible through either DocType; mismatches require a rebuild from MariaDB. |
| 1.12 | `_sync_embedding_dimension` via `probe_dimension` | Dimension learned from the first successful fixed-model request | **Redesign** | `probe_dimension()` calls Ollama and persists the returned width; it never resolves an `AI Model`. See ADR 0016. |
| 1.13 | Model-then-provider credential resolution order | same | **Port** | |

---

## 2. Agent Tier

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 2.1 | `Flow Agent` DocType | `AI Agent` | **Port** | Plus new Agno fields — see 2.12. |
| 2.2 | `assemble()` → runtime agent | `AgentBuilder.build()` → `agno.agent.Agent` | **Adapt** | Same pattern, different runtime class; runs in FastAPI. |
| 2.3 | `DEFAULT_TOOL_SLUGS = (describe, read, execute)` seeded on insert | same | **Port** | |
| 2.4 | `_ensure_knowledge_search_tool()` — auto-append `search_knowledge` when KBs bound | same | **Port** | |
| 2.5 | Permission gate in `assemble()` (agent enabled, `AI Model` read perm, model enabled) | same, in `AgentBuilder` | **Adapt** | Check happens in Frappe before config is released to the service. |
| 2.6 | `_resolve_tools()` — missing tool logged + skipped, disabled skipped | same | **Port** | |
| 2.7 | Bound tools: `search_knowledge`→KB list, `update_memory`→agent name | same | **Port** | Binding config-side, never model-side. |
| 2.8 | `max_iterations` enforcement | same | **Port** | Enforced in the Agno loop. |
| 2.9 | `Flow Agent Tool` child table | `AI Agent Tool` | **Port** | |
| 2.10 | `Flow Agent Knowledge Base` child table | `AI Agent Knowledge Base` | **Port** | |
| 2.11 | `is_system_generated` immutability guards | same | **Port** | Via `utils/system_generated.py`. |
| 2.12 | — | `temperature`, `top_p`, `reasoning`, `markdown`, `agent_type` | **New** | Agno capabilities `flow` had no field for. |
| 2.13 | — | `mcp_connections` child table | **New** | See §8. |

---

## 3. Tools

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 3.1 | `Flow Tool` — Imported / Script types | `AI Tool` | **Port** | Both types retained. |
| 3.2 | Slug regex `^[a-z][a-z0-9_]*$`, unique | same | **Port** | The name the LLM sees. |
| 3.3 | Script validation: AST parse, top-level `main`, no `*args`/`**kwargs`, no self-call | same | **Port** | |
| 3.4 | `schema_from_code()` — JSON Schema from AST **without evaluating** | same | **Port** | Untrusted code never executes during schema derivation. |
| 3.5 | `@tool` decorator + pydantic `validate_call` | Agno's tool decorator + pydantic | **Adapt** | Agno provides equivalent runtime arg validation. |
| 3.6 | `build_schema()` — `Annotated`, `Optional`, `Literal`→enum, unions→`anyOf`, pydantic models, `Enum` | Agno schema generation | **Adapt** | Verify parity on `Literal` and nested pydantic during Phase 3. |
| 3.7 | Script tools via `safe_exec` with `ai_tool_arg_N` injection | same, **hardened namespace** | **Redesign** | Fixes a real security asymmetry — see [ADR 0006](../decisions/0006-unified-safe-exec-namespace.md). |
| 3.8 | Imported tools via `frappe.get_attr(import_path)` | same | **Port** | Executes in Frappe. |
| 3.9 | `requires_confirmation` → pause the run | same | **Port** | Pause/resume mediated by the service, decided by the DocType flag. |
| 3.10 | `confirm_prompt` lambdas producing plain-English summaries | same | **Port** | `_summarize_values` truncation rules included. |
| 3.11 | Tool exceptions → `{"error": …}` truncated to 500 chars | same | **Port** | A failing tool never kills the run. |

### 3.12 The ten builtin tools

All ten are preserved, exposed to Agno as schemas but **executed inside Frappe** via the
dispatch endpoint.

| Tool | Confirms | Kind | Notes |
|---|---|---|---|
| `find_doctypes` | no | **Port** | Excludes child tables; per-row `has_permission`. |
| `describe` | no | **Port** | Fields + user's CRUD flags; with `name`, adds docstatus and available actions. |
| `read` | no | **Port** | `frappe.get_list`, permission-respecting, capped at 200. |
| `search_knowledge` | no | **Adapt** | Same contract; LanceDB-backed, hybrid retained. Fails closed on empty KB list. |
| `update_memory` | no | **Port** | Bound to the agent; unbound fails closed. |
| `create` | **yes** | **Port** | Per-record insert; partial success via `failures`. |
| `update` | **yes** | **Port** | Per-record permission check + full validation. |
| `delete` | **yes** | **Port** | Per-record permission check. |
| `run_action` | **yes** | **Port** | submit/cancel/amend/rename/workflow/whitelisted method. |
| `execute` | **yes** | **Port** | Arbitrary sandboxed Python via the hardened namespace. |

| # | Feature | Kind | Notes |
|---|---|---|---|
| 3.13 | `sync_builtin_tools()` upserting all ten as system-generated rows | **Port** | Uses `db.set_value` on update to bypass the immutability guard. |

---

## 4. Conversation & Audit Tier

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 4.1 | `Flow Session` (hash naming, `modified DESC`) | `AI Session` | **Port** | |
| 4.2 | `Flow Session Message` child table | `AI Session Message` | **Port** | role, content, tool_call_id, tool_calls, run link. |
| 4.3 | `Flow Session Attachment` child table | `AI Session Attachment` | **Port** | Inline / Retrieval modes. |
| 4.4 | `Flow Run` — status, iterations, input/output/error, tool_calls, questions, usage, config_snapshot | `AI Run` | **Port** | The audit trail is `flow`'s best feature; preserved verbatim. |
| 4.5 | `apply_result()` — **accumulates** iterations and usage (resume-safe) | same | **Port** | Appends only delta messages. |
| 4.6 | Run invariants (Paused ⇒ questions; Failed ⇒ error) | same | **Port** | |
| 4.7 | `_assert_session_owner` / `assert_run_owner` | same | **Port** | Critical: persistence uses `ignore_permissions=True`. |
| 4.8 | Agent locked after session creation | same | **Port** | Model override still allowed. |
| 4.9 | `derive_title` from first message | same | **Port** | |
| 4.10 | `_build_prompt_messages` — system msg, inline file text, retrieval notes, top-8 chunks, `<agent_memory>` | same | **Port** | Assembled in Frappe, sent to the service. |
| 4.11 | `transcript_html` rendering | same | **Port** | Desk-side form view. |
| 4.12 | `clear_old_logs(days=30)` batched cleanup | same | **Adapt** | Also purges LanceDB rows. |
| 4.13 | `default_log_clearing_doctypes = {"Flow Session": 90}` | same for `AI Session` | **Port** | |
| 4.14 | `submit_feedback` (Up/Down + comment ≤500) | same | **Port** | |
| 4.15 | Thumbs-down + comment → shared agent memory | same | **Port** | |
| 4.16 | `stream_with_persistence` commit choreography | — | **Drop** | WSGI-specific; unnecessary under FastAPI. See [001 §8](001-architecture.md). |
| 4.17 | Stale-run recovery (300s auto-fail, `recover_session`, `stop_run`) | same + service-side cancel | **Adapt** | New: cancelling the in-flight service task. |

---

## 5. Knowledge / RAG

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 5.1 | `Flow Knowledge Base` | `AI Knowledge Base` | **Port** | `description` is shown to the LLM to decide when to search. |
| 5.2 | `Flow Knowledge Source` — Text / File / URL / DocType | `AI Knowledge Source` | **Port** | |
| 5.3 | `Flow Knowledge Chunk` — autoincrement naming as vector join key | `AI Knowledge Chunk` | **Adapt** | Name remains the LanceDB row `id`. Naming rule is load-bearing. |
| 5.4 | Extraction: pdf (pdfplumber + table→markdown + OCR fallback), xlsx, docx, html, text | same | **Port** | RapidOCR at 200 DPI. |
| 5.5 | `_validate_public_url` SSRF guard | same | **Port** | Rejects private/loopback/link-local/reserved/multicast. |
| 5.6 | 10 MB `_read_capped` limit | same | **Port** | |
| 5.7 | DocType source `content_fields` validated against meta | same | **Port** | Blocks injection; rejects child-table fields. |
| 5.8 | Character chunker with overlap, whitespace-aware | same | **Port** | |
| 5.9 | Embedding via litellm, batched at 96, order-preserving | `frappe_ai.knowledge.embedder`, fixed Ollama OpenAI-compatible call | **Redesign** | Every call uses provider `ollama` and model `nomic-embed-text`; only `FRAPPE_AI_OLLAMA_BASE_URL` varies. Batching/order-preservation remains. |
| 5.10 | `probe_dimension()` | same | **Redesign** | Performs one real fixed-model request and persists its width in `AI Settings`; no `AI Model` is involved. |
| 5.11 | LanceDB `chunks` table | same | **Port** | [ADR 0002](../decisions/0002-lancedb-vector-store.md) |
| 5.12 | **Hybrid search (BM25 + vector)** | same | **Port** | Preserved by staying on LanceDB. `search_type` defaults to `Hybrid`. |
| 5.13 | Incremental DocType sync: `last_synced_at` watermark + SHA-256 `content_hash` | same | **Port** | |
| 5.14 | `_remove_stale` via `Deleted Document` tombstones | same | **Port** | |
| 5.15 | `reconcile_source()` full scan for tombstone-less deletes | same | **Port** | |
| 5.16 | `resync(rebuild=False)` whitelisted | same | **Port** | |
| 5.17 | `retrieve()` fails closed on empty KB list | same | **Port** | Never widened to "all". |
| 5.18 | Disabled KBs filtered at query time | same | **Port** | Disabling a KB is a real off-switch. |
| 5.19 | KB binding as the authorization boundary (chunks not re-checked per user) | same | **Port** | Documented limitation, carried forward intentionally. |
| 5.20 | Attachment chunks in a separate LanceDB table | same | **Port** | Same ephemeral semantics. |
| 5.21 | `stage_attachment` 1h cache; validation at upload time | same | **Port** | |
| 5.22 | `sync_due_sources` daily scheduler job | same | **Port** | |
| 5.23 | Ingestion on `queue="long"`, `enqueue_after_commit=True` | same | **Port** | |
| 5.24 | Code-first `Knowledge(title)` handle with `add_text/add_file/add_url/add_doctype` | same | **Port** | Synchronous ingestion path. |

---

## 6. Triggers & Automation

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 6.1 | `Flow Trigger` DocType | `AI Trigger` | **Port** | |
| 6.2 | Wildcard `doc_events "*"` on 5 events | same | **Port** | after_insert, on_update, on_submit, on_cancel, on_trash. |
| 6.3 | Self-doctype recursion guard | same | **Port** | Skips the app's own DocTypes. |
| 6.4 | Skip during install/migrate | same | **Port** | |
| 6.5 | Condition evaluated **as `run_as`** at dispatch | same | **Port** | Avoids under-firing on permission-sensitive conditions. |
| 6.6 | **Re-evaluation** of the condition inside `fire` | same | **Port** | Guards state drift between enqueue and execution. |
| 6.7 | Condition failure ⇒ treated as not met (fail-closed) | same | **Port** | |
| 6.8 | Jinja `prompt_template` rendered with `{doc, now}`, sandboxed env | same | **Port** | |
| 6.9 | `run_as` identity for the whole run, `try/finally` restore | same | **Port** | |
| 6.10 | Scheduled triggers: `*/5` cron + croniter anchored on `last_fired_at or creation` | same | **Port** | |
| 6.11 | `last_fired_at` set with `update_modified=False` | same | **Port** | |
| 6.12 | `auto_approve` bypassing confirmations for unattended runs | same | **Port** | |
| 6.13 | Enqueue on default queue, `enqueue_after_commit=True` | same | **Port** | |
| 6.14 | Trigger execution in-process | POST to FastAPI | **Adapt** | Same run path as interactive chat. |

---

## 7. Agent Memory

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 7.1 | `Flow Agent Memory` — Agent / User scope | `AI Agent Memory` | **Port** | |
| 7.2 | 500-char cap, `MAX_ACTIVE_MEMORIES = 100` per bucket | same | **Port** | Forces consolidation. |
| 7.3 | `build_memory_block` only when the agent has `update_memory` | same | **Port** | |
| 7.4 | ≤20 memories ⇒ inject all | same | **Port** | |
| 7.5 | >20 ⇒ relevance selection, top-12 | same | **Port** | LanceDB BM25 FTS retained. See [ADR 0002](../decisions/0002-lancedb-vector-store.md). |
| 7.6 | Degrade to recency if search fails | same | **Port** | |
| 7.7 | `<agent_memory>` block labelled "treat as data, not instructions" | same | **Port** | Prompt-injection hardening. |
| 7.8 | `User` scope stamped server-side from session user | same | **Port** | Never model-supplied. |
| 7.9 | `source_run` from run context flag | same | **Adapt** | Carried on the dispatch call instead of `frappe.flags`. |
| 7.10 | Index errors never block a memory write | same | **Port** | |

---

## 8. MCP (new capability)

| # | Feature | Kind | Notes |
|---|---|---|---|
| 8.1 | `AI MCP Connection` — stdio / SSE | **New** | Per the reference specification. |
| 8.2 | `AI Agent MCP Connection` child table | **New** | Binds connections to agents. |
| 8.3 | JSON auto-population of connection config | **New** | `create_mcp_connection_from_json()`. |
| 8.4 | `check_connection()` / `check_all_mcp_connections()` | **New** | 5s timeout per connection. |
| 8.5 | Status fields: `is_connected`, `last_check_time`, `status_message` | **New** | Read-only, scheduler-updated. |
| 8.6 | `*/5` scheduler health probe | **New** | |
| 8.7 | Pre-flight MCP check during agent build | **New** | Warn and degrade, do not hard-fail. |
| 8.8 | MCP tools surfaced to Agno | **New** | Agno has native MCP support. |

> **Security note:** MCP `stdio` connections execute shell commands from a DocType field.
> This is a privileged capability — restrict `AI MCP Connection` write permission to
> System Manager and treat it as equivalent to server-script access.

---

## 9. UI / UX

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 9.1 | Vue 3 + frappe-ui + Tailwind panel, 25 components | **React + TypeScript desk/page frontend** | **Redesign** | Vue/Vite was dropped. `frappe_ai` now uses a checked-in React app and shared component/state runtime. |
| 9.2 | Vite → single IIFE bundle, mtime cache-busting | **esbuild → committed JS/CSS bundles, mtime cache-busting** | **Adapt** | Output is `frappe_ai/public/frappe_ai_panel/frappe_ai_panel.js|css`. |
| 9.3 | Cmd/Ctrl+I to open; fullscreen default; resizable; persisted state | same | **Port** | Implemented for the desk slide-in panel. |
| 9.4 | `api/client.js` via `frappe.xcall` / `frappe.client.get_list` | **frontend-oriented JSON BFF layer** | **Redesign** | New same-origin contract lives under `frappe_ai.api.frontend.*`; current UI targets that layer first. |
| 9.5 | `api/stream.js` SSE against Frappe with CSRF token | **Frappe start/resume + FastAPI stream with bearer token** | **Adapt** | The wire contract remains FastAPI bearer-token streaming; the client parses the stream manually via `fetch`. |
| 9.6 | `extend_bootinfo` supplying supported file types | same | **Port** | Kept as a source of truth, though bootstrap/API loading is now preferred for custom frontend shells. |
| 9.7 | Confirmation cards, activity steps, feedback bar, sessions menu | same | **Port** | Present in React form. |
| 9.8 | Slide-in desk panel only | **desk panel + full page + custom frontend-targeted API** | **New** | `/app/frappe-ai` now mounts a full-page React shell, but that page shell is not yet final UI architecture. |

> **Note:** `apps/flow/flow/public/` contains only `.gitkeep` — the panel bundle is unbuilt
> in this checkout. The Vue **source** under `apps/flow/frontend/src/` is what gets ported.

> **Implementation note (2026-08-10):** the new page route mounts the current React
> markup correctly, but page mode is still reusing a shell originally designed for the
> slide-in panel. The unresolved work is therefore a host-surface/layout problem, not an
> API or stream-contract problem.

---

## 10. Assistant

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 10.1 | Built-in `Flow` assistant agent, 40 iterations | `Frappe AI` assistant | **Pending** | `frappe_ai.assistant` has not been implemented yet; this remains a parity item. |
| 10.2 | ~50-line operating-doctrine system prompt | — | **Pending** | The operating-doctrine prompt is not currently shipped. |
| 10.3 | `sync_builtin_assistant` on `after_migrate` and on first model insert | guarded hook only | **Pending** | `AI Model.after_insert` has an ImportError guard; `hooks.py` does not currently register the sync. |
| 10.4 | Refreshes instructions, appends missing tools, **never removes user-added tools**; bails if the user cleared `is_system_generated` | — | **Pending** | Must be implemented with the assistant agent. |
| 10.5 | No-op when no enabled model exists | — | **Pending** | Behaviour to verify when the assistant agent is implemented. |

---

## 11. Cross-cutting

| # | `flow` feature | `frappe_ai` | Kind | Notes |
|---|---|---|---|---|
| 11.1 | `utils/safe_exec.py` hardened namespace (excludes `frappe.db.sql`, `frappe.qb`, `frappe.db.set_value`, `frappe.get_all`) | same | **Port** | Gated `get_doc`, `get_list` forcing `user=session.user`, etc. |
| 11.2 | **Two divergent `safe_exec` implementations** | one | **Redesign** | [ADR 0006](../decisions/0006-unified-safe-exec-namespace.md) — Script tools and conditions move to the hardened namespace. |
| 11.3 | `utils/conditions.py` — expression or `result`-assigning script | same | **Port** | `_assigns_result` control-flow walk. |
| 11.4 | `utils/system_generated.py` — `validate_immutable`, `block_delete`, `block_rename` | same | **Port** | Compares against **DB** values so flag-tampering cannot disarm guards. |
| 11.5 | `ignore_links_on_delete` for chunk/run/session | same | **Port** | |
| 11.6 | Undeclared runtime deps (pydantic, croniter, jinja2, bs4, lxml, openpyxl, chardet, requests, RestrictedPython) | **declared explicitly** | **Redesign** | `flow` relied on transitive availability. |
| 11.7 | `patches/rename_ai_to_flow.py` incl. `__Auth` repointing | — | **Drop** | Greenfield; no rename history. See [ADR 0005](../decisions/0005-greenfield-no-migration.md). |
| 11.8 | 12 test modules | mirrored | **Port** | agent, api, assistant, builtins, model, resolver, tool, triggers, conditions, knowledge, safe_exec, session. |

---

## 12. Summary

| Kind | Count |
|---|---|
| Port | ~86 |
| Adapt | ~16 |
| Redesign | 7 |
| New | 8 original MCP items + Assistant Core/FAC migration items + 2 agent tuning fields |
| Drop | 2 |

**The two drops**, both deliberate:

1. `stream_with_persistence` commit choreography — a WSGI workaround with no FastAPI analogue.
2. The `rename_ai_to_flow` patch — no rename history exists in a greenfield app.

**The seven redesigns** are the real cost of this migration, and each is documented:

1. **Script-tool sandbox unification** (3.7, 11.2) — Script tools and trigger conditions
   move from frappe's broad namespace to the hardened one.
   → [ADR 0006](../decisions/0006-unified-safe-exec-namespace.md)
2. **Explicit dependency declaration** (11.6) — `flow` relied on transitive availability.
3. **Trigger execution transport** (6.14) — in-process execution becomes a POST to the
   service. Detection logic itself is unchanged.
4. **Provider validation** (1.1, 1.2) — chat execution is resolved against Agno-supported
   provider slugs; litellm is used only for provider validation and model suggestions.
   → [ADR 0009](../decisions/0009-no-litellm-agno-native-models.md),
   [ADR 0013](../decisions/0013-litellm-for-provider-ux-agno-still-executes.md)
5. **Model id composition** (1.5) — `model_id` is a bare id resolved via the linked
   `AI Provider`, not a litellm-parsed `provider/model` string. → ADR 0009
6. **Context window** (1.6) — dropped from auto-detected to plain user-editable, since
   Agno has no `litellm.get_model_info()` equivalent. → ADR 0009
7. **Connection test** (1.8) — explicit fresh capability suite through the shared
   OpenAI-compatible transport, with synthetic-only tool testing and blocked
   dependent checks. → ADR 0015

Items 4–7 are all instances of the same underlying redesign — keeping Agno as the sole
chat execution layer while narrowing litellm to provider/model UX and validation —
counted separately here because each touches a distinct row above. The final split is
documented in [ADR 0009](../decisions/0009-no-litellm-agno-native-models.md) and
[ADR 0013](../decisions/0013-litellm-for-provider-ux-agno-still-executes.md).

> **Note:** an earlier draft planned a move to ChromaDB, which would have added four more
> redesigns — losing hybrid search, degrading memory recall to keyword matching, and
> rewriting both the chunk and attachment stores. Staying on LanceDB
> ([ADR 0002](../decisions/0002-lancedb-vector-store.md)) turned all four back into
> straight ports. The knowledge pipeline is now the *least* risky part of this migration
> rather than the most.
