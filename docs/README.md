# frappe_ai documentation

This is the map. If you are new, read **[Start here](#start-here)**, then follow the **[reading order](#reading-order-for-a-new-developer)**.

## Start here

`frappe_ai` lets users chat with AI agents inside Frappe. Agents can search knowledge, use tools that read and change records, remember things, and be started by triggers.

The app is split into two programs:

- **Frappe** keeps all configuration and data, checks permissions, and **runs every tool**.
- A small **FastAPI service** talks to the language model and streams the answer to the browser. It keeps no data of its own.

Four rules explain most of the design. When something is unclear, decide it against these.

1. **Frappe authorizes, FastAPI orchestrates.** The service never reads Frappe's database. Each tool call goes back to Frappe and runs as the user who started the run, so Frappe's normal permission checks apply. [ADR 0003](decisions/0003-tools-execute-in-frappe.md)
2. **MariaDB is the truth, LanceDB is a cache.** Knowledge text lives in `AI Knowledge Chunk`. LanceDB only holds search vectors and can always be rebuilt. [ADR 0002](decisions/0002-lancedb-vector-store.md)
3. **All generated code runs in one hardened sandbox.** `execute`, script tools and trigger conditions all use `frappe_ai/utils/safe_exec.py`. [ADR 0006](decisions/0006-unified-safe-exec-namespace.md)
4. **Permissions limit *what*, budgets limit *how much*.** Even a permitted agent should not make unlimited calls. [ADR 0008](decisions/0008-execution-budgets.md)

## Life of one chat message

1. The browser calls `start_run` (Frappe). Frappe saves the user's message, creates an `AI Run` and returns a short-lived **run token** and the service URL.
2. The browser opens a streaming request to the service (`POST /stream/{run}`) with that token.
3. The service checks the token, then asks Frappe for the run's configuration (agent, model, tools, conversation so far).
4. It builds an agent and calls the model. Text streams to the browser as it arrives.
5. If the model wants a tool, the service asks Frappe to run it (`dispatch_plugin_tool`). Frappe checks the run, the user, any needed approval and the budgets, then runs the tool as that user and returns the result.
6. If the tool needs the user's approval, the run **pauses**. The user clicks Approve or Deny (`resume_run`) and the stream continues.
7. At the end the service sends the final result back to Frappe (`persist_run_result`), which stores messages, usage and status on the `AI Run`.

## Reading order for a new developer

About 2–3 hours for the core path. Each step lists what to open and what you should understand afterwards.

| # | Read | You will learn |
|---|---|---|
| 1 | This page, then [001 Architecture](specifications/001-architecture.md) | The two programs and how a request flows |
| 2 | `frappe_ai/api/api.py` (`start_run`, `resume_run`, `stop_run`) | How a run starts and how tokens are issued |
| 3 | `frappe_ai/service/main.py`, `service/auth.py` | How the service accepts a stream |
| 4 | `frappe_ai/api/service.py` (`get_run_config`) | Everything the service is told about one run |
| 5 | `frappe_ai/service/builder.py` | How config becomes an agent, and how approval pauses work |
| 6 | `frappe_ai/service/routes/chat.py` | The run loop and the streaming events |
| 7 | `frappe_ai/api/dispatch.py`, `api/budgets.py` | The security boundary for tool calls |
| 8 | `frappe_ai/frappe_ai/doctype/ai_run/ai_run.py`, `ai_session/ai_session.py` | How results are stored |
| 9 | [003 DocTypes](specifications/003-doctype-reference.md) | The data model |
| 10 | [008 Creating tools](specifications/008-how-to-create-tools.md), `tools/builtins.py`, `utils/safe_exec.py` | Tools and the sandbox |
| 11 | [007 MCP and Assistant Core](specifications/007-mcp-integration-and-cleanup.md), `api/mcp.py` | External tools |
| 12 | `frappe_ai/knowledge/` in the order `ingest → chunker → embedder → store → retriever`, plus `memory/memory.py` | Knowledge search and memory |
| 13 | `frappe_ai/triggers/triggers.py`, `hooks.py` | Starting agents without a browser |
| 14 | [005 Frontend contract](specifications/005-frontend-contract.md), `frontend/src/` | How the UI uses the API |
| 15 | `frappe_ai/tests/` (`test_chat_route.py`, `test_builder.py`, `test_api.py`) | The intended behaviour, as executable examples |

To make it stick, trace one scenario: *a user asks the agent to delete a record*. Follow it from `start_run` through the pause in `chat.py`, the approval stored by `resume_run`, and the check in `dispatch_plugin_tool`.

## Specifications (what the system does)

| Doc | Contents |
|---|---|
| [001 Architecture](specifications/001-architecture.md) | Components, request lifecycle, authentication, streaming format, failures, deployment |
| [002 Feature map](specifications/002-feature-mapping.md) | Which module and DocType implements each feature |
| [003 DocType reference](specifications/003-doctype-reference.md) | Every DocType, its fields and purpose |
| [004 Session and model switching](specifications/004-session-model-switching.md) | Locking an agent to a session, changing the model |
| [005 Frontend contract](specifications/005-frontend-contract.md) | Endpoints, streaming events, host adapters for the UI |
| [006 Dynamic MCP profiles](specifications/006-dynamic-mcp-server-profiles.md) | An old proposal that was **not** built |
| [007 MCP and Assistant Core](specifications/007-mcp-integration-and-cleanup.md) | How external tools are connected |
| [008 Creating tools](specifications/008-how-to-create-tools.md) | Step-by-step recipe |
| [009 FAC admin-style UI](specifications/009-fac-admin-style-ui-guide.md) | UI conventions for the admin screens |
| [010 Known gaps](specifications/010-review-topics.md) | Open questions and unfinished items |
| [011 Model capability tests](specifications/011-ai-model-capability-testing.md) | The "Test Connection" check on `AI Model` |

## Decisions (why it is this way)

Each decision record is short: the problem, the choice, the consequences.

| ADR | Decision |
|---|---|
| [0001](decisions/0001-agno-fastapi-over-frappe-native.md) | Run agents in a separate FastAPI service built on Agno, not inside Frappe workers |
| [0002](decisions/0002-lancedb-vector-store.md) | LanceDB for search vectors; MariaDB stays the source of truth |
| [0003](decisions/0003-tools-execute-in-frappe.md) | Tools run inside Frappe as the acting user |
| [0004](decisions/0004-sse-direct-from-fastapi.md) | The browser streams directly from the service |
| [0005](decisions/0005-greenfield-no-migration.md) | New app, no data migrated from the older `flow` app |
| [0006](decisions/0006-unified-safe-exec-namespace.md) | One sandbox for all generated code |
| [0007](decisions/0007-failure-over-durable-execution.md) | A restart fails a run cleanly instead of resuming it mid-way |
| [0008](decisions/0008-execution-budgets.md) | Per-run limits on calls, changes, records and time |
| [0011](decisions/0011-service-secret-in-site-config.md) | The shared secret lives in `site_config.json` |
| [0013](decisions/0013-litellm-for-provider-ux-agno-still-executes.md) | litellm only for provider/model suggestions; it never makes the calls |
| [0014](decisions/0014-openai-compatible-chat-transport.md) | One OpenAI-compatible transport for all chat models |
| [0015](decisions/0015-configuration-time-model-capability-tests.md) | Model capabilities are tested on demand, not on every run |
| [0016](decisions/0016-fixed-ollama-embeddings.md) | One fixed embedding model (`nomic-embed-text` on Ollama) |

## Guides and history

| Doc | Purpose |
|---|---|
| [Setup](setup.md) | Install, run the service, embeddings, backups, rebuilding the index |
| [MCP setup guide](MCP_INTEGRATION_SETUP_GUIDE.md) | Connect an MCP server to an agent |
| [Frappe Assistant Core integration](FRAPPE_ASSISTANT_CORE_INTEGRATION.md) | Using FAC tools from agents |
| [DocType cleanup plan](DOCTYPE_CLEANUP_PLAN.md) | Which legacy DocTypes remain and why |
| [Learnings](learnings.md) | Surprises met while building, and what fixed them |
| [Progress notes](progress/) | What was built, and what is still open |
| [Architecture review](reviews/2026-09-30-architecture-review.md) | Strengths, risks and the fix list |

## Glossary

| Term | Meaning |
|---|---|
| **Agent** | An `AI Agent` record: instructions, a model, the tools it may use, and optional knowledge bases |
| **Session** | One conversation (`AI Session`). It stores the messages and is locked to one agent |
| **Run** | One turn of a conversation (`AI Run`): the user message through to the final answer. Status: Running, Paused, Completed or Failed |
| **Tool / tool call** | An action the model can request, such as "read these records". The model asks, Frappe does it |
| **Confirmation / approval** | Some tools need the user to click Approve first. The run pauses until they do |
| **Run token** | A short-lived signed token the browser uses to open the stream for one run |
| **SSE** | Server-Sent Events: a one-way stream of events from server to browser |
| **Agno** | The Python agent library the service uses to run the model-and-tools loop |
| **MCP** | Model Context Protocol: a standard way to expose tools from another program |
| **FAC** | Frappe Assistant Core: a separate Frappe app with a registry of tools that agents here can use |
| **RAG** | Retrieval-augmented generation: finding relevant text first and giving it to the model |
| **Knowledge base** | A named set of documents split into chunks and indexed for search |
| **Chunk** | A piece of a document (about 1,000 characters by default) |
| **Embedding** | A list of numbers representing the meaning of a text, used for similarity search |
| **LanceDB** | The embedded database holding embeddings and a keyword index |
| **Trigger** | A rule that starts an agent on a document event, a schedule, or a call from app code |
| **Budget** | Limits for one run: tool calls, changes, records per call, active time |
| **Sandbox / `safe_exec`** | A restricted Python environment for code the model writes |

## How to write docs here

- **Specifications** (`specifications/NNN-*.md`) describe how things work *now*. When behaviour changes, edit the spec.
- **Decisions** (`decisions/NNNN-*.md`) record why. Once accepted, do not rewrite them; write a new one and mark the old one `Superseded by NNNN` (or delete it if nothing links to it).
- **Progress notes** are short and honest about what is not done.
- Use plain words, define a term when you first use it, and check names (fields, endpoints, files) against the code.
