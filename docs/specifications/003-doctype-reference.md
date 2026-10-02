# 003 — DocType reference

All `frappe_ai` data is stored in DocTypes (Frappe record types). There are 22. This page explains what each one is for, its fields, and the rules its controller enforces. Layout-only fields (section and column breaks) are left out.

**Conventions**

- *Naming* says how a record's name (its primary key) is built: `field:<x>` uses that field, `hash` is a random id, `autoincrement` is a counter.
- *System Manager* has full access to every DocType here unless stated. Other roles are listed per DocType.
- Child tables (tables inside another DocType) inherit permissions from their parent.

## Map

| Group | DocTypes |
|---|---|
| **Models** | `AI Provider`, `AI Model`, `AI Settings` |
| **Agents** | `AI Agent` and its child tables `AI Agent Tool`, `AI Agent Knowledge Base`, `AI Agent MCP Connection`, `AI Agent Plugin Tool`, `AI Agent Tool Config` |
| **Tools** | `AI Tool`, `AI FAC Tool`, `AI MCP Connection`, `AI MCP Tool` |
| **Conversations** | `AI Session`, `AI Session Message`, `AI Session Attachment`, `AI Run` |
| **Knowledge** | `AI Knowledge Base`, `AI Knowledge Source`, `AI Knowledge Chunk` |
| **Automation and memory** | `AI Trigger`, `AI Agent Memory` |

Only `AI Settings` is a Single (one record per site). None are submittable.

---

## Models

### AI Provider

Credentials and endpoint for a model provider (OpenAI, Anthropic, Ollama, …). *Naming:* `field:provider`, lower-cased so the name matches the provider slug.

| Field | Type | Notes |
|---|---|---|
| `provider` | Data | required, unique |
| `enabled` | Check | default 1 |
| `api_key` | Password | |
| `base_url` | Data | must be `http(s)` |
| `extra_params` | JSON | extra client options; reserved keys are rejected |

`validate` checks the provider against litellm's provider list (with aliases where litellm and Agno disagree), the base URL, and `extra_params`. litellm is used only for this validation and for model-id suggestions, never to call a model ([ADR 0013](../decisions/0013-litellm-for-provider-ux-agno-still-executes.md)).

### AI Model

One chat model that an agent can use. Embeddings are *not* a DocType: they use one fixed Ollama model configured by environment ([ADR 0016](../decisions/0016-fixed-ollama-embeddings.md)). *Naming:* `field:title`. Version history is on.

| Field | Type | Notes |
|---|---|---|
| `title` | Data | required, unique |
| `enabled` | Check | default 1 |
| `is_default` | Check | at most one enabled default; used as the fallback model |
| `provider` | Link → AI Provider | optional |
| `model_id` | Autocomplete | required; the bare id (`gpt-4o-mini`), no `provider/` prefix |
| `context_window` | Int | typed in by hand, not detected |
| `api_key`, `base_url` | Password, Data | used **only when `provider` is empty** |
| `params` | JSON | extra call parameters; reserved keys are rejected |

How the connection details are chosen (`_model_call_config` in `api/service.py`): if `provider` is set, key and base URL come from that provider; if it is empty, the model's own `api_key` and `base_url` are used. Either way the call goes through the one shared OpenAI-compatible client ([ADR 0014](../decisions/0014-openai-compatible-chat-transport.md)).

`test_connection()` (whitelisted, needs write permission) runs the capability checks for the saved model; see [011](011-ai-model-capability-testing.md). It never runs during normal chat. `get_provider_models(provider)` returns litellm's model suggestions. *Permissions:* everyone can **read** (a user needs to read the model bound to their agent); System Manager has full access.

### AI Settings (Single)

Site-wide settings for the service and the knowledge pipeline.

| Field | Default | Notes |
|---|---|---|
| `service_base_url` | `http://127.0.0.1:8001` | Where Frappe and the browser reach the service |
| `request_timeout` | 120 s | Non-streaming service requests |
| `stream_timeout` | 600 s | Streaming connection limit |
| `search_type` | `Hybrid` | `Hybrid` (vector + keyword) or `Vector` |
| `chunk_size`, `chunk_overlap` | 1000, 200 | Chunking defaults; overlap must be smaller than size |
| `embedding_dimension` | — | read-only; recorded from the first embedding request |
| `lancedb_path`, `service_status` | — | read-only indicators |

There is no secret here. The shared secret is `frappe_ai_service_secret` in `site_config.json` ([ADR 0011](../decisions/0011-service-secret-in-site-config.md)). *Permissions:* System Manager read/write.

---

## Agents

### AI Agent

An agent: instructions plus a model plus what it may use. *Naming:* `field:title`. Version history on. Deleting and renaming are blocked.

| Field | Default | Notes |
|---|---|---|
| `title` | | required, unique |
| `enabled` | 1 | |
| `model` | | required, Link → AI Model |
| `instructions` | | required; becomes the system prompt |
| `agent_type` | `Agent` | `Agent` or `Team` |
| `max_iterations` | 10 | Passed to the agent library as a cap on tool calls per run |
| `max_tool_calls`, `max_mutations`, `max_records_per_call`, `max_runtime_seconds` | 50, 20, 100, 600 | Run budgets ([ADR 0008](../decisions/0008-execution-budgets.md)) |
| `temperature` | 0.7 | Sent to the model, except for agents with `reasoning` on |
| `top_p` | 1.0 | Sent only when different from 1.0 |
| `reasoning` | 0 | Marks a reasoning model; sampling parameters are then not sent |
| `markdown` | 1 | Ask the model to format answers as Markdown |
| `knowledge_bases` | | table → AI Agent Knowledge Base |
| `plugin_tools` | | table → AI Agent Plugin Tool (Assistant Core tools) |
| `mcp_connections` | | table → AI Agent MCP Connection |
| `tools` | | table → AI Agent Tool Config; **legacy**, kept for compatibility |

Controller: `validate` requires `max_iterations ≥ 1`, adds the `search_knowledge` tool when knowledge bases are bound, and fills MCP tool metadata. `_snapshot()` produces the settings snapshot stored on every run. At run time the service builds the agent from the run's config ([001](001-architecture.md)). Frappe checks the agent is enabled and the user may read the model before releasing any config. *Permissions:* everyone can read.

### Child tables of an agent

| DocType | Fields | Purpose |
|---|---|---|
| `AI Agent Knowledge Base` | `knowledge_base` (Link, required) | Which knowledge bases the agent can search |
| `AI Agent Plugin Tool` | `fac_tool` (Link → FAC Tool Configuration, required), `requires_confirmation` (default **1**), `enabled` (default 1) | Binds an Assistant Core tool, and says whether each call needs the user's approval. **This row is what Frappe checks when it decides if a call needs approval** |
| `AI Agent MCP Connection` | `mcp_connection` (Link, required), `include_tools` (JSON list, optional), `tools_list` (read-only) | Which MCP servers, and optionally which of their tools |
| `AI Agent Tool` | `tool` (Link → AI Tool, required) | Legacy binding of an `AI Tool` |
| `AI Agent Tool Config` | `tool_name`, `source` (`mcp`/`fac`/`manual`), `description`, `enabled` | Legacy metadata kept during the move to Assistant Core |

---

## Tools

### AI Tool

A tool definition stored as a record. *Naming:* `field:slug`; the slug is the name the model sees. Now mostly legacy: new tools are bound as Assistant Core tools, but the built-in tools (`read`, `create`, …) are still `AI Tool` rows created by `sync_builtin_tools` on `migrate`.

| Field | Notes |
|---|---|
| `title`, `slug` | slug required and unique, matching `^[a-z][a-z0-9_]*$` |
| `type` | `Imported` (a Python function by `import_path`) or `Script` (code in `code`) |
| `enabled`, `requires_confirmation`, `is_system_generated` | |
| `description` | Long text **shown to the model**; say when to use the tool |
| `summary` | Short text for people |
| `import_path`, `code` | One of the two, according to `type` |

A Script tool must define a top-level `main`, may not use `*args` or `**kwargs`, and may not call `main()` itself. `type` and `import_path` cannot change after creation. Tools **execute in Frappe**, never in the service; the service only receives the JSON Schema. Script tools run in the `safe_exec` sandbox ([ADR 0006](../decisions/0006-unified-safe-exec-namespace.md)). The schema is derived from the code's syntax tree **without running it**. See [008](008-how-to-create-tools.md).

### AI FAC Tool

A local list of Assistant Core tools. *Naming:* `field:tool_name`. Fields: `tool_name` (unique), `category` (`Core`/`Custom`/`Workflow`/`Data`/`Search`/`Automation`), `description`, `enabled`.

### AI MCP Connection

A connection to a Model Context Protocol server. *Naming:* `field:connection_name`. **Privileged:** a `stdio` connection starts a program on the server. Only System Manager can write it.

| Field | Notes |
|---|---|
| `connection_name` | required, unique |
| `connection_type` | `stdio`, `SSE` or `streamable-http` |
| `command` | stdio only: the executable alone (one token, no spaces or shell characters) |
| `command_args` | stdio only: JSON list of strings |
| `environment_variables` | JSON object; names like `[A-Za-z_][A-Za-z0-9_]*`, **string values** |
| `endpoint_url` | SSE and streamable-http only; must be an `http(s)` URL |
| `api_key`, `api_secret` | Password fields (streamable-http authentication) |
| `mcp_config` | **Import only**: a pasted `mcpServers` JSON is copied into the fields above on save, then cleared |
| `enabled` | default 1 |
| `is_connected`, `last_check_time`, `status_message` | read-only; set by the health check |
| `tools` | table → AI MCP Tool |

`validate` enforces these formats. A legacy one-line `command` such as `python -m pkg` is split into the executable and its arguments on save. Whitelisted: `check_connection(name)`, `check_all_mcp_connections()` (System Manager only), `get_mcp_health_dashboard()`, `create_mcp_connection_from_json(config)` (needs create permission). A 5-minute scheduled job checks every enabled connection, with a 5-second timeout each.

### AI MCP Tool (child of AI MCP Connection)

One tool discovered on an MCP server: `tool_name`, `description`, `input_schema`, `available`, `last_discovered`, `matched_ai_tool` (read-only link when an `AI Tool` has the same name), `raw_metadata`.

---

## Conversations

### AI Session

One conversation. *Naming:* `hash`, newest first.

| Field | Notes |
|---|---|
| `title` | taken from the first message |
| `agent` | **locked** after creation |
| `model` | optional override; may change between turns, but not while a run is Paused or Running |
| `source` | `Manual` or `Trigger`, read-only |
| `messages` | table → AI Session Message (hidden, read-only) |
| `attachments` | table → AI Session Attachment (read-only) |

Deleting a session also deletes its runs and the attachment vectors in both MariaDB and LanceDB. `clear_old_logs(days=30)` removes old runs, messages, files and sessions in batches of 100. Ownership is checked by `assert_session_owner` (owner, or `write` permission); this check matters because the service callbacks write with `ignore_permissions=True`. `assert_not_blocked()` refuses a new turn while a run is Paused, and fails a *Running* run older than 300 s. *Permissions:* everyone can create, read, write and delete **their own** sessions.

**AI Session Message** (child): `role` (required), `content`, `tool_call_id`, `tool_calls` (JSON), `run`. This is the transcript stored in OpenAI message format.

**AI Session Attachment** (child): `file`, `file_name`, `file_size`, `run`, `mode` (`Inline` puts the extracted text in the prompt; `Retrieval` splits large files into chunks searched through the temporary `chat_attachment_chunks` table), `extracted_text`.

### AI Run

One turn, and the audit record. *Naming:* `hash`.

| Field | Notes |
|---|---|
| `session` | required |
| `source`, `trigger`, `reference_doctype`, `reference_name` | how it started (`Manual` or `Trigger`) |
| `status` | `Running`, `Paused`, `Completed`, `Failed` |
| `iterations`, `input`, `output`, `error` | |
| `tool_calls`, `usage` | JSON, added up across resumes |
| `questions` | pending approvals while Paused |
| `approvals` | the user's recorded approvals: call id → tool name and a hash of the arguments. Written **only** by `resume_run`; each entry is used up once by tool dispatch |
| `segment_started_at` | start of the current active period (run creation, or the latest resume). The runtime budget is measured from here, so time spent Paused does not count |
| `budget_usage` | counters for calls, changes, records |
| `config_snapshot` | the agent's settings at run start, including `auto_approve` for trigger runs |
| `feedback_rating`, `feedback_comment` | thumbs up/down from the user |

Rules in the controller:

- `validate`: JSON fields must be valid; a Paused run needs questions; a Failed run needs an error message.
- `apply_result(result)` sets the status, **adds** iterations and usage, and appends only the new messages to the session.
- `mark_failed(error)` stores the error (cut to 5000 characters).
- **A Completed or Failed run is final.** Both methods raise `RunAlreadyFinished`; the service callbacks turn that into an "ignored" reply. A late result cannot overwrite a stopped run.
- `assert_run_owner` (owner, or `write` permission) guards access.

*Permissions:* everyone can read their own runs.

---

## Knowledge

A knowledge base (KB) is a named set of documents. Documents are split into chunks, each chunk gets an embedding, and the agent can search them.

### AI Knowledge Base

*Naming:* `field:title`. Fields: `title`, `enabled`, `is_system_generated`, `description` (**shown to the model** to help it decide when to search). **Access model:** a KB is reachable only through the agents it is attached to, so *access to the agent is the permission*. Chunks are not re-checked per user, and DocType sources are read without per-document permission checks. Only index records that everyone with access to that agent may see.

### AI Knowledge Source

One document or data set inside a KB. *Naming:* `hash`.

| Field | Notes |
|---|---|
| `title`, `knowledge_base` | `knowledge_base` and `source_type` cannot change later |
| `source_type` | `Text`, `File`, `URL` or `DocType` |
| `content` / `file` / `url` | the input, according to the type |
| `reference_doctype`, `filters`, `content_fields`, `auto_sync` | for `DocType` sources: which records and fields, and whether the daily sync keeps them current |
| `chunk_size`, `chunk_overlap` | 0 means use the settings value |
| `status` | `Pending`, `Processing`, `Completed`, `Failed` (read-only) |
| `chunk_count`, `last_synced_at`, `error_log` | read-only; `last_synced_at` is the watermark for incremental sync |

On insert, ingestion is queued on the `long` queue after commit. Deleting a source purges its MariaDB chunks and its LanceDB rows. Whitelisted: `resync(rebuild=False)`, `reconcile()`. URL sources are checked against private, loopback, link-local and reserved addresses and limited to 10 MB.

### AI Knowledge Chunk

A piece of a source. *Naming:* **`autoincrement`, and that matters**: the integer name is the LanceDB row id, so changing it silently breaks retrieval.

Fields (all read-only): `knowledge_base`, `source`, `chunk_index`, `reference_doctype`, `reference_name`, `content`, `content_hash` (SHA-256, used to skip unchanged chunks on sync). MariaDB holds the authoritative text; LanceDB holds only vectors keyed by this id and can be rebuilt. System Manager can read and report, nothing else.

---

## Automation and memory

### AI Trigger

Starts an agent automatically or from app code. *Naming:* `field:title`.

| Field | Notes |
|---|---|
| `title`, `enabled`, `agent` | |
| `event` | `DocType Event`, `Scheduled`, or `Manual` |
| `target_doctype`, `doc_event` | for DocType events: `after_insert`, `on_update`, `on_submit`, `on_cancel`, `on_trash` |
| `cron_expression`, `last_fired_at` | for scheduled triggers |
| `condition` | optional Python condition, run in the sandbox |
| `prompt_template` | required Jinja template; gets `doc` (DocType events) or `context` (manual) and `now` |
| `run_as` | user the run acts as; must be enabled and not Guest. Defaults to the trigger's owner |
| `auto_approve` | **skips the approval pause for the whole run.** Only a System Manager may save a trigger with it on |

Rules: the condition is evaluated as the `run_as` user when the event happens, and evaluated **again** when the job runs, in case the document changed. A condition error counts as "not met". Every trigger run adds a note to the agent's instructions that text from documents is data, not instructions.

### AI Agent Memory

A fact an agent keeps. *Naming:* `hash`. System Manager only in the Desk; agents write through the `update_memory` tool.

| Field | Notes |
|---|---|
| `agent`, `scope` | `Agent` (everyone) or `User` (one person; required when scope is `User`, and always set by the server from the session user, never by the model) |
| `status` | `Active` or `Archived` |
| `source`, `source_run` | `Agent`, `Feedback` or `Manual`; read-only |
| `content` | at most 500 characters |
| `keywords` | helps recall |

At most 100 active memories per agent and scope. When 20 or fewer exist, all are put in the prompt; above that a keyword search over the user's latest message picks up to 12, plus the 3 most recently touched. The injected `<agent_memory>` block is labelled as data, not instructions. The keyword index (`memories` table, no vectors) is kept in sync on save and delete; index failures are logged and never block a write.

---

## Permissions at a glance

| DocType | System Manager | Everyone else |
|---|---|---|
| AI Provider, Tool, FAC Tool, MCP Connection, Trigger, Agent Memory, Knowledge Base/Source | full | none |
| AI Settings | read, write | none |
| AI Model, AI Agent | full | **read** |
| AI Session | full | create/read/write/delete **own** |
| AI Run | full | read **own** |
| AI Knowledge Chunk | read, report | none |

## Naming at a glance

| Rule | DocTypes |
|---|---|
| `field:<x>` | Provider, Model, Agent, Tool, Trigger, Knowledge Base, MCP Connection, FAC Tool |
| `hash` | Session, Run, Knowledge Source, Agent Memory |
| `autoincrement` | **Knowledge Chunk** (name = LanceDB row id) |

`ignore_links_on_delete` is set for `AI Knowledge Chunk`, `AI Run` and `AI Session`, so those can be removed with their parents.
