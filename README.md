# Frappe AI

AI agents inside Frappe. Users chat with an agent in the Desk (a slide-in panel or a full page). The agent can search company knowledge, read and change Frappe records through tools, remember facts, and be started automatically by triggers. Every run is stored in Frappe for audit.

## How it is built, in one picture

```
 Browser (React panel/page)
    │  1. start_run / resume_run / stop_run        (normal Frappe API calls)
    │  2. open the answer stream directly          (SSE, with a short-lived run token)
    ▼                                   ▼
┌──────────────────────┐        ┌──────────────────────────┐
│ Frappe  (port 8000)  │ ◄───── │ FastAPI service (8001)   │
│                      │  HTTP  │                          │
│ • AI DocTypes: agents│        │ • builds an Agno agent   │
│   models, sessions,  │        │ • calls the LLM          │
│   runs, knowledge    │        │ • streams tokens (SSE)   │
│ • permissions        │        │ • asks Frappe to run     │
│ • runs every tool    │        │   each tool call         │
│ • knowledge + memory │        │                          │
│   (LanceDB index)    │        │ stateless, no database   │
└──────────────────────┘        └──────────────────────────┘
```

**Why two processes?** An LLM call can take minutes. If Frappe waited for it, it would tie up a web worker for the whole time. The FastAPI service does the waiting, so the Desk stays responsive.

**Who decides what?** Frappe decides what a user is allowed to do. The service only orchestrates. Every tool call goes back to Frappe and runs *as the user who started the run*, so an agent can never touch data that user could not touch.

## Where to start reading

Open [docs/README.md](docs/README.md). It has a reading order, a glossary and links to every document. The shortest path to understanding the app is to follow one chat message from the browser to the model and back (the guide in `docs/README.md` lists the files in order).

## Repository map

| Path | What is in it |
|---|---|
| `frappe_ai/frappe_ai/doctype/` | The DocTypes (agents, models, sessions, runs, knowledge, triggers, MCP connections…) and their controllers |
| `frappe_ai/api/` | Whitelisted Frappe endpoints: starting runs, tool dispatch, callbacks from the service, the frontend API, MCP checks |
| `frappe_ai/service/` | The FastAPI service: entry point, run-token check, agent builder, the streaming chat route |
| `frappe_ai/lib/` | Model/provider configuration and the tool resolver |
| `frappe_ai/tools/`, `assistant_tools/` | Built-in tools and Frappe Assistant Core (FAC) tool wrappers |
| `frappe_ai/knowledge/` | Knowledge pipeline: extract → chunk → embed → store → retrieve |
| `frappe_ai/memory/` | Agent memory |
| `frappe_ai/triggers/` | Starting agents from document events, schedules or app code |
| `frappe_ai/utils/` | Sandbox for generated code (`safe_exec`), trigger conditions |
| `frappe_ai/patches/` | One-off data migrations |
| `frappe_ai/tests/` | Unit and integration tests |
| `frontend/` | React/TypeScript source, built into `frappe_ai/public/frappe_ai_panel/` |
| `docs/` | Everything written down: specifications, decisions, guides |

## Install

Requires a Frappe bench (v15) with Python 3.10 or newer.

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench --site <site> install-app frappe_ai
```

Then follow [docs/setup.md](docs/setup.md): it covers the shared secret, running the FastAPI service next to `bench start`, the Ollama embedding model, and the frontend build.

Optional: for faster, higher-quality PDF text extraction install Docling in the bench environment (`bench pip install "docling>=1.0.0"`). Without it the app uses `pdfplumber` plus RapidOCR automatically.

## Run the tests

```bash
bench --site <site> run-tests --app frappe_ai
```

## Contributing

The repo uses `pre-commit` (ruff, eslint, prettier, pyupgrade):

```bash
cd apps/frappe_ai
pre-commit install
```

## Status

Core features work: chat with streaming and tool approvals, knowledge search, memory, triggers, MCP and Assistant Core tools. Production hardening is not finished (rate limiting, SSE heartbeats, observability, resilience of the service's HTTP client). See [docs/reviews/2026-09-30-architecture-review.md](docs/reviews/2026-09-30-architecture-review.md) for the honest list.

## License

MIT
