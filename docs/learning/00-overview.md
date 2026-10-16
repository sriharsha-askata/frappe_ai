# Lesson 0 - The big picture

## What the app does
`frappe_ai` adds AI agents to Frappe/ERPNext. A user picks an **agent** (saved model +
instructions + allowed tools) and chats. The model can search company documents,
call Frappe tools as the logged-in user, pause for approval of risky actions, keep
memory across chats, and run unattended from triggers.

## Vocabulary (dictionary-of-ai-coding)
- **Harness** - software around the model (tools, system prompt, context, permissions) that makes it an agent. This repo is a harness.
- **Agent** - model + harness + tools + system prompt + context window.
- **Tool call** - the model's output naming a tool and arguments; "just structured text" until the harness executes it.
- **Context window** - everything the model sees in one request.
- **System prompt** - standing instructions added to every request (`AI Agent.instructions`).
- **Token** - unit models read/write; drives cost, speed and limits.
- **Human in the loop** - a person approves risky actions (`Paused` status, Approve/Deny).

Key idea: the model never does anything itself. Most of this repo is the code that decides whether to run what the model asked for.

## Architecture
```
Browser (React panel / SPA)
  | 1. POST frappe_ai.api.frontend.start_run (session cookie)
  v
FRAPPE (8000): api/ validates, creates AI Run, mints run token
  DocTypes in MariaDB; api/dispatch.py runs tools AS THE USER
  ^ 4. config, tool calls, results (HTTP + shared secret)     | 2. {token, stream_url}
  |                                                           v
FASTAPI (8001): main.py POST /stream/{run}  <- 3. browser opens SSE with Bearer token
  builder.py builds Agno Agent from the run's config snapshot
  routes/chat.py runs the agent loop and streams events
  |-> LLM provider (OpenAI-compatible)   |-> LanceDB + Ollama (knowledge)
```
Why two processes: a chat run waits on the model for tens of seconds; a Frappe worker handles one request at a time (ADR 0001, 0004). Trust rule: FastAPI never touches the DB or holds credentials; every Frappe action goes back to Frappe, which applies the user's permissions (ADR 0003).

## Repository map (dependency order)
| Area | Path | Role |
|---|---|---|
| Entry and trust | `service/main.py`, `config.py`, `auth.py`, `api/api.py` | boot, tokens |
| Service <-> Frappe | `service/frappe_client.py`, `api/service.py` | callbacks |
| Agent assembly | `service/builder.py` | config -> Agno agent |
| Loop | `service/routes/chat.py`, `agno_events.py`, `transcript.py` | run and stream |
| Approvals | `service/routes/confirmation.py` | human in the loop |
| Tools | `api/dispatch.py`, `assistant_tools/`, `api/fac_tools.py`, `api/mcp.py` | execution |
| Limits | `api/budgets.py`, `AI Run` | caps |
| Knowledge | `knowledge/` | extract, chunk, embed, store, retrieve |
| Memory | `memory/` | cross-chat facts |
| Triggers | `triggers/` | unattended runs |
| Data | `frappe_ai/doctype/*` | 22 DocTypes |
| Retirement | `api/retirement.py`, `migration.py`, `patches/` | one-off migration (skip first pass) |
| UI / quality | `frontend/`, `tests/`, `docs/` | |
