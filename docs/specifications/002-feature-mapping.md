# 002 — Feature map

Where each feature lives in the code. Use it to find the right file fast. For how the pieces fit together, read [001 Architecture](001-architecture.md); for fields, [003](003-doctype-reference.md).

(An older app called `flow` ran agents inside Frappe's own workers. `frappe_ai` reimplements its feature set on a new runtime. The old app is not needed to use this one.)

## Models and settings

| Feature | Where | Notes |
|---|---|---|
| Provider records (keys, base URL) | `AI Provider` (`doctype/ai_provider/`) | Provider name validated with litellm; used only for validation and suggestions |
| Model records | `AI Model` (`doctype/ai_model/`) | Bare `model_id`; `context_window` is typed in by hand |
| Choosing the connection details for a model | `_model_call_config` in `api/service.py`, `lib/model.py` (`resolve_model_config`) | Provider set → provider's key and URL; otherwise the model's own |
| The one chat client | `lib/model.py` (`create_openai_compatible_model`) | Bounded retries and timeouts; error messages normalized by `normalize_provider_error` |
| Model capability check | `doctype/ai_model/connection_test.py`, `ai_model.js` | See [011](011-ai-model-capability-testing.md) |
| Fallback model | `AI Model.is_default`, `chat.py` | Used once if the first model fails before any tool ran |
| Site settings | `AI Settings` | Service URL, timeouts, chunking, search type |
| Embeddings | `knowledge/embedder.py` | One fixed Ollama model ([ADR 0016](../decisions/0016-fixed-ollama-embeddings.md)) |

## Agents

| Feature | Where |
|---|---|
| Agent definition | `AI Agent` (`doctype/ai_agent/`) |
| Building the runtime agent | `service/builder.py` (`AgentBuilder.build`) |
| Sampling settings and iteration cap | `service/builder.py` (`_with_generation_params`, `tool_call_limit`) |
| Releasing an agent's config to the service | `api/service.py` (`get_run_config`) after checking the agent, model and owner |
| Protection of system-created records | `utils/system_generated.py` (`validate_immutable`, `block_delete`, `block_rename`) |

## Tools

| Feature | Where | Notes |
|---|---|---|
| Tool definitions as records | `AI Tool` | `Imported` (function by path) or `Script` (stored code) |
| Turning a record into a callable | `lib/resolver.py` (`resolve_tool`) | |
| JSON Schema from code, without running it | `lib/resolver.py` (`schema_from_code`) | |
| JSON Schema from a Python function | `lib/tool.py` (`tool`, `build_schema`) | |
| The ten built-in tools | `tools/builtins.py` | `find_doctypes`, `describe`, `read`, `search_knowledge`, `update_memory`, `create`, `update`, `delete`, `run_action`, `execute`. The last five need approval |
| Syncing built-ins | `sync_builtin_tools` (runs after `migrate`) | |
| The code sandbox | `utils/safe_exec.py` | Used by `execute`, script tools and trigger conditions |
| Assistant Core (FAC) tools | `assistant_tools/native.py`, `api/fac_tools.py`, `AI Agent Plugin Tool`, `AI FAC Tool` | Bound per agent; the binding row carries the approval flag |
| MCP servers | `AI MCP Connection`, `api/mcp.py`, `service/builder.py` (`_build_mcp_tools`) | See [007](007-mcp-integration-and-cleanup.md) |
| Running a tool as the user | `api/dispatch.py` | The security boundary |
| Run limits | `api/budgets.py` | [ADR 0008](../decisions/0008-execution-budgets.md) |
| Approval pause and resume | `service/builder.py`, `service/routes/chat.py`, `api/api.py` (`resume_run`), `AI Run.approvals` | |

## Conversations and audit

| Feature | Where |
|---|---|
| Sessions, messages, attachments | `AI Session`, `AI Session Message`, `AI Session Attachment` |
| One turn of a run, results, status | `AI Run` (`apply_result`, `mark_failed`, `create_run`) |
| Starting, resuming, stopping | `api/api.py` (`start_run`, `resume_run`, `stop_run`, `recover_session`) |
| Run token | `service/auth.py` |
| Service callbacks | `api/api.py` (`persist_run_result`, `fail_run`, `log_diagnostic`) |
| Streaming | `service/main.py`, `service/routes/chat.py` |
| Thumbs feedback | `api/api.py` (`submit_feedback`); a `Down` with a comment becomes an agent memory |
| Mid-session model switch | `_resolve_session` in `api/api.py` ([004](004-session-model-switching.md)) |
| Old-log cleanup | `AISession.clear_old_logs` |

## Knowledge and memory

| Feature | Where |
|---|---|
| Knowledge base and sources | `AI Knowledge Base`, `AI Knowledge Source` |
| Reading files, URLs, DocType records | `knowledge/extract.py` (PDF uses Docling if installed, else `pdfplumber` + RapidOCR) |
| Splitting into chunks | `knowledge/chunker.py` |
| Creating embeddings | `knowledge/embedder.py` |
| Index (LanceDB) | `knowledge/store.py` |
| Ingest, sync, rebuild | `knowledge/ingest.py`, `knowledge/migration.py` (`rebuild_knowledge_index`) |
| Search | `knowledge/retriever.py` (`retrieve`), tool `search_knowledge` |
| Chat attachments over the size limit | `knowledge/attachment_store.py`, `AI Session Attachment.mode = Retrieval` |
| Agent memory | `AI Agent Memory`, `memory/memory.py`, `memory/store.py`, tool `update_memory` |

## Automation

| Feature | Where |
|---|---|
| Trigger records | `AI Trigger` |
| Document events and schedule | `hooks.py` (`doc_events["*"]`, 5-minute cron) → `triggers/triggers.py` (`dispatch`, `dispatch_scheduled`, `fire`) |
| Manual triggers from app code | `triggers/triggers.py` (`fire_manual_trigger`) |
| Trigger conditions | `utils/conditions.py` (run in the sandbox) |
| Daily knowledge sync | `hooks.py` → `knowledge/ingest.py` (`sync_due_sources`) |
| MCP health checks | `hooks.py` → `api/mcp.py` (`check_all_mcp_connections`) |

## User interface

| Feature | Where |
|---|---|
| JSON API for the UI | `api/frontend.py` ([005](005-frontend-contract.md)) |
| React app | `frontend/src/` (state in `state/store.tsx`, components in `components/`) |
| Desk panel and full page | `frontend/src/hosts/` |
| Built bundle loaded by the Desk | `public/frappe_ai_panel/`, included through `hooks.py` (`app_include_js`/`css`) |
| File types the composer accepts | `boot.py` (from `knowledge/extract.py`) |
| Workspace shortcut | `workspace/frappe_ai/` (fixture) |
