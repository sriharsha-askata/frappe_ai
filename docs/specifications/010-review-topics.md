# 010 — Known gaps and open questions

A plain list of what is not finished or not decided. It is meant to be honest, not complete; for the long form see the [architecture review](../reviews/2026-09-30-architecture-review.md). Update it when something changes.

## Production hardening (not done)

These are needed before heavy or public use:

- **No rate limiting** on `start_run` or the service.
- **No SSE heartbeats**, so a proxy that closes idle connections can cut a long tool call.
- **The service's HTTP client** opens a new connection per request and does not retry (retrying a tool call safely also needs an idempotency key, which does not exist).
- **No tracing or metrics.** Diagnostics go to Frappe's Error Log; there is no request id across Frappe and the service.
- **Trigger runs hold a Frappe background worker** for the whole run, because the job reads the service's stream to the end. Fine for a few manual triggers, a problem with many event-driven ones. The planned fix is a "start and return" mode in the service.
- **Budgets:** counters are updated without a lock (parallel tool calls can overshoot a limit), and "what counts as a change" is a fixed list of tool names plus tools needing approval. MCP calls bypass budgets and approvals entirely.
- **One shared secret** signs run tokens and authenticates the service to Frappe, with no rotation procedure. Run tokens can be reused within their 300-second life.
- **The service reads one site's configuration.** Running several sites needs one service per site.
- **A leaked secret** lets an attacker act as any user who currently has an active run. The planned fix is a per-run credential for service-to-Frappe calls instead of the global secret.

## Decisions taken, so they are not re-opened by accident

- **Knowledge access = agent access.** A knowledge base is available to everyone who can use an agent it is attached to. Chunks and DocType sources are not filtered per document.
- **Agent locked to a session; model switchable** between turns ([004](004-session-model-switching.md)).
- **One fixed embedding model** (Ollama `nomic-embed-text`).
- **Approvals are enforced in Frappe**, not only in the service.

## Open questions

### 1. Falling back when a model runs out of context

Today there is one retry: if the first model call fails before any tool has completed, the turn is retried once on the default model (`AI Model.is_default`). There is no automatic switch when a conversation gets too long for the model's context window, and switching models in the middle of a run is risky (a paused run may then resume under a different provider). Needs a design.

### 2. Default model when creating an agent

`AI Agent.model` is required, but the form does not pre-fill the site's default model. Easy to add; not done.

### 3. Remote MCP and the budget/approval gap

How should calls to MCP servers get the same approvals and counters as direct tools? Options include routing them through Frappe or wrapping them in the service with Frappe-side accounting. See [007](007-mcp-integration-and-cleanup.md).

### 4. Tender automation tools

Direct Assistant Core tools exist for all ten tender capabilities and the three tender agents use them, with the MCP connection kept as a fallback. Before removing the MCP connection, verify that Spec Review, Historical Match and SAP Match each complete through the direct tools with a real model.

### 5. LanceDB at scale

LanceDB runs embedded on the site's files, with one writer discipline (Frappe background workers). It is not proven for several web or worker machines, large corpora, or fast backups. If you need that, evaluate a server-based vector store behind the same `store.py` interface.

## Reference: where session data lives

Sessions, messages and attachments are MariaDB rows (`AI Session` and its child tables). `AI Run` stores per-turn status, output, usage, tool calls, questions and the settings snapshot. Only retrieval chunks of oversized attachments are in LanceDB. Lifecycle protections: owner checks, agent locking, model-enabled validation, blocking while a run is Paused or Running, recovery of runs stuck *Running* for more than 300 seconds, explicit stop and recover endpoints, and periodic clean-up of old sessions (`AISession.clear_old_logs`).
