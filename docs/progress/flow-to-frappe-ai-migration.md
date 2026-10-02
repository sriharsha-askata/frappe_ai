# Project status

Where `frappe_ai` stands. It started as a rebuild of the older `flow` app on a new runtime (see [ADR 0001](../decisions/0001-agno-fastapi-over-frappe-native.md) and [ADR 0005](../decisions/0005-greenfield-no-migration.md)). This page is a short, current summary. Older phase-by-phase logs were condensed into the decisions and specifications they produced.

**Overall:** the core runtime works. Reconciliation with Assistant Core and production hardening are not finished. Treat it as suitable for local development, internal evaluation and staging with non-production data, **not yet for production traffic**.

## What was built

| Area | State |
|---|---|
| Models, providers, settings | Done. One OpenAI-compatible client for all chat models ([ADR 0014](../decisions/0014-openai-compatible-chat-transport.md)); capability test on request ([011](../specifications/011-ai-model-capability-testing.md)) |
| Shared sandbox, trigger conditions | Done ([ADR 0006](../decisions/0006-unified-safe-exec-namespace.md)) |
| FastAPI service | Done: run tokens, shared secret from `site_config.json` ([ADR 0011](../decisions/0011-service-secret-in-site-config.md)), streaming chat route |
| Agents, tools, run loop | Done: agent builder, Frappe-side dispatch, SSE, approval pause and resume with approve, deny and redirect (live-tested; see [learnings](../learnings.md)) |
| Knowledge (RAG) | Done: extract → chunk → embed → LanceDB → hybrid search; fixed Ollama embeddings ([ADR 0016](../decisions/0016-fixed-ollama-embeddings.md)) |
| Triggers, memory, MCP | Done: DocType-event, scheduled and manual triggers; agent memory; MCP connections (`stdio`, `SSE`, `streamable-http`) |
| Frontend | React app built with esbuild, as a slide-in panel and a full page at `/app/frappe-ai` ([005](../specifications/005-frontend-contract.md)). Open: a dedicated full-page layout (the page still reuses panel-first styling) and a full parity check across panel, page and custom frontends |
| Mid-session model switching | Done ([004](../specifications/004-session-model-switching.md)) |
| Security hardening (2026-09/10) | Done: dispatch bound to a live run owned by the user; approvals recorded and enforced in Frappe; run state guards; validated MCP connection fields and permission checks; trigger `auto_approve` restricted to System Manager with an untrusted-content note. See the [architecture review](../reviews/2026-09-30-architecture-review.md) |

## Still open

**Finish the Assistant Core move** (details: [progress note](ai-tool-retirement-via-assistant-core.md), [007](../specifications/007-mcp-integration-and-cleanup.md)):
- verify the three tender workflows (Spec Review, Historical Match, SAP Match) through direct Assistant Core tools, then remove the tender MCP connection;
- prove an exact Assistant Core equivalent for every production `AI Tool`, with parity tests for `execute` and `run_action`;
- retire the legacy tool records only after those gates.

**Reconcile with the older app and the docs:** a full green test suite, the 25-simultaneous-streams check that the Desk stays responsive, the pre-uninstall checklist in [ADR 0005](../decisions/0005-greenfield-no-migration.md), and then uninstalling `flow`.

## Production hardening (do before production traffic)

Safety critical:
- **SSE heartbeats** (a `ping` every ~15 s) so streams survive proxies with short idle timeouts. Not built.
- **Rate limiting** per user and per agent on run starts and tool dispatch, with a tighter limit for trigger runs. Not built.
- **Bounded retry** with backoff on model 429/5xx/timeouts (the client already retries up to twice; a policy that also records retries on the run does not exist).
- **Budgets for MCP calls.** Direct tool calls are counted; MCP calls are not ([ADR 0008](../decisions/0008-execution-budgets.md)). Atomic counters are also open.
- **Trigger runs** should not hold a Frappe worker for the whole run.

Operability:
- **Trace ids** across the browser, Frappe and the service, and structured logs.
- **Metrics** (runs, failures, latency, tokens).

Later: cost accounting per run, user quotas, prompt and tool-schema versioning, replayable streams (only if heartbeats are not enough), evaluation sets, provider routing, retrieval quality metrics.

The fuller list with reasons is in [010](../specifications/010-review-topics.md) and the [architecture review](../reviews/2026-09-30-architecture-review.md).

## Decided against (do not reopen without new evidence)

| Idea | Why not | Where |
|---|---|---|
| Resume runs in the middle after a restart | Repeating side effects is hard; retrying is cheap | [ADR 0007](../decisions/0007-failure-over-durable-execution.md) |
| A worker pool for the service | Runs wait on I/O; async concurrency is the scaling method | [ADR 0001](../decisions/0001-agno-fastapi-over-frappe-native.md) |
| A pluggable multi-backend vector store | An abstraction written against one backend is usually wrong; `store.py` already isolates LanceDB | [ADR 0002](../decisions/0002-lancedb-vector-store.md) |
| Silent provider fallback in the middle of a conversation | It changes model behaviour without telling the user. A single retry on the default model *before any tool ran* is the only fallback | `service/routes/chat.py` |

## Environment notes

Database-backed tests need a bench with MariaDB and Redis available. Tests that call real model providers need valid credentials; the end-to-end tender workflow checks were blocked on a model credential in the last environment.
