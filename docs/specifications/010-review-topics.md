# Topics to Review - 2026-08-20

**Review status:** Initial implementation review completed 2026-08-21. The findings
below are current-state notes; remaining work is explicitly called out rather than left
as “unknown.”

## 1. LanceDB Usage & Configuration

**Finding:** LanceDB is initialized by `frappe_ai.knowledge.store` and
`frappe_ai.memory.store` with `lancedb.connect()`. The site-scoped path is
`sites/<site>/private/files/lancedb`, or `lancedb_test` during tests.

It stores three derived tables:

- `chunks`: knowledge embeddings, metadata, and FTS content.
- `chat_attachment_chunks`: temporary embeddings for oversized attachments.
- `memories`: vectorless FTS data for agent-memory relevance search.

Sessions, messages, runs, and authoritative chunk text remain in MariaDB-backed Frappe
DocTypes. The LanceDB store can be rebuilt from MariaDB knowledge chunks.

**Files to check:**
- frappe_ai app
- frappe_assistant_core app
- Any related configuration files

---

## 2. Token Fallback to Default Model

**Requirement:** When a model runs out of tokens during a run, it should automatically fall back to the default model.

**Current state:** Not implemented. Token usage is recorded on `AI Run`, and
`frappe_ai.lib.model.get_default_model()` can find an enabled default, but no service
error handler currently switches models after a context/token-limit failure. This needs
a separate design because switching models mid-run changes provider behavior and may
invalidate a paused confirmation state.

**Files to check:**
- `frappe_ai/api/service.py` - model handling
- `frappe_ai/service/builder.py` - agent building
- Any token tracking or quota management code

---

## 3. Default Model on Agent Creation

**Requirement:** When creating a new agent, automatically set the default model first. User can change later.

**Current state:** Not implemented. `AI Model.is_default` enforces one enabled default,
but `AI Agent.model` is still required and agent creation does not populate it from the
default automatically.

**Files to check:**
- `frappe_ai/doctype/ai_agent/ai_agent.py` - agent creation
- `frappe_ai/doctype/ai_agent/ai_agent.json` - agent fields

---

## 4. Session Storage & Management

**Finding:** Sessions are stored in MariaDB through the `AI Session` DocType. Messages
and attachments are child rows; `AI Run` stores per-turn status, output, usage, tool
calls, questions, and configuration snapshots. Large attachment retrieval chunks are
the only session-related data stored in LanceDB.

Lifecycle management includes owner checks, agent locking, model-enabled validation,
paused/running guards, stale-running recovery after 300 seconds, explicit stop/recover
endpoints, and periodic old-session cleanup.

**Files to check:**
- `frappe_ai/doctype/ai_session/` - session DocType
- `frappe_ai/doctype/ai_session_message/` - messages
- Any session handling code

---

## 5. Tender Automation Tools Review

**Current state:** Direct Assistant Core/FAC registrations and bindings are implemented
for all ten tender capabilities. The three tender agents retain their MCP connections
as fallback, and duplicate MCP tools are filtered when a direct FAC tool is available.

The remaining decision gate is workflow verification: Spec Review, Historical Match, and
SAP Match must each complete successfully through direct FAC tools before the Tender MCP
connection is removed. A valid model credential is still required for full LLM-driven
verification.

**Options:**
1. Refine existing tender_automation tools
2. Use FAC (Assistant Core) tools which provide better results

**Action needed:**
- Document current tender_automation tools
- Compare with FAC tools
- Decide which approach to use

**Files to check:**
- `tender_automation/` app - custom tools
- `frappe_assistant_core` - FAC tools
- Current tool implementations

---

## Related Specifications

| Spec | Status | Notes |
|------|--------|-------|
| 001-architecture | Review needed | May cover session storage |
| 007-mcp-integration | ~40% done | FAC tools integration |
| 008-how-to-create-tools | Complete | Reference for tool creation |
