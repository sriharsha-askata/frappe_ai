# Production Readiness Review — 2026-09-10

**Status:** Complete (review), In progress (remediation)
**Reviewer scope:** Whole-app review of `frappe_ai` as built, against Frappe
framework behaviour, Agno 2.8.7 as installed, and `frappe_assistant_core` as
installed.
**Baseline:** Working tree at branch point `d08b04d`, including in-flight
uncommitted work. Remediation lands on `production-hardening-p0-p1`.

> **A note on the review's premise.** The commissioning brief described
> `frappe_ai` as an integration layer *into* Frappe Assistant, with Assistant
> owning conversations and UI. That is not what the code does. `frappe_ai` owns
> `AI Session`, `AI Session Message`, `AI Run`, and its own React panel;
> `frappe_assistant_core` is consumed as a **tool and MCP provider**. This review
> assesses the architecture as actually built, and flags duplication where it is
> real rather than assumed.

---

## 1. Executive Summary

`frappe_ai` is a well-architected application with a small number of severe
defects, most of which were invisible because the test suite was broken in a way
that masked them.

The two-process design (Frappe owns config, persistence, permissions and tool
execution; a FastAPI sidecar owns the Agno run loop and streams SSE to the
browser) is sound and well-documented. The 18 existing ADRs are unusually high
quality: they state trade-offs honestly, and where the code diverges from them it
is nearly always the code that is wrong, not the ADR. The security model in
[ADR 0003](../decisions/0003-tools-execute-in-frappe.md) — every Frappe-touching
tool executes in Frappe under `frappe.set_user(acting_user)` — is the right
model and is correctly implemented on the paths it covers.

Against that, the review found one **authenticated remote code execution** chain,
a **budget bypass on precisely the mutating path budgets exist to bound**, a
feature (**scheduled triggers**) that had never worked at all, and three
configuration fields that were silently discarded. Separately, an incomplete
field rename from the Assistant Core migration had left **every agent showing
zero tools in the UI** and every run's audit snapshot with an empty tool list.

The single most important structural finding is not any individual bug — it is
that **the test suite had rotted to the point of being unable to detect them**.
366 tests ran with 62 broken, and the dominant failure mode (an agent could not
be created at all) meant most integration coverage never executed its assertions.
Two real production bugs were sitting directly behind that wall.

**Deployment verdict: NO — see §18.** With the P0 remediation in this branch
applied, the remaining blockers are narrow and enumerable.

### Disposition of findings

| ID | Finding | Severity | Status |
|---|---|---|---|
| F-1 | Authenticated RCE via MCP connection create + check | P0 | **Fixed** (`b25d09c`) |
| F-2 | Budget bypass on confirmation-approve path | P0 | **Fixed** (`b25d09c`) |
| F-3 | Unprotected whitelisted methods (incl. read-only users forcing re-embedding) | P0 | **Fixed** (`b25d09c`) |
| F-4 | MCP tools act as a shared connection identity | P0 | **Decided** — [ADR 0019](../decisions/0019-mcp-acting-user-identity.md); escalation vector closed |
| F-5 | Scheduled triggers never ran, and consumed their window on failure | P1 | **Fixed** (`65e2fb9`) |
| F-6 | `max_iterations` never reached Agno; loop unbounded | P1 | **Fixed** (`65e2fb9`) |
| F-7 | `temperature` / `top_p` silently discarded | P1 | **Fixed** (`65e2fb9`) |
| F-8 | Field rename fallout: zero tools in UI, empty audit snapshot | P1 | **Fixed** (`b25d09c`) |
| F-9 | Test suite unable to create an agent; 62 broken tests | P1 | **Fixed** (`b25d09c`) |
| F-10 | Knowledge retrieval performs no permission checks | P1 | **Open** |
| F-11 | MCP tool calls bypass execution budgets | P1 | **Open** (pre-existing) |
| F-12 | `budgets.consume` is an unlocked read-modify-write | P1 | **Open** |
| F-13 | Error Log records tool arguments and prompt context | P1 | **Open** |
| F-14 | Wildcard `doc_events` puts frappe_ai in every site write | P2 | **Open** |
| F-15 | Trigger runs block an RQ worker polling SSE | P2 | **Open** (pre-existing) |

---

## 2. Current Architecture

Two processes, one database, one authorization boundary.

**Frappe (`:8000`)** owns everything durable and everything privileged:
`AI Agent`/`AI Model`/`AI Provider` configuration, `AI Session` +
`AI Session Message` transcripts, `AI Run` execution records, credentials, the
knowledge corpus, and — critically — **all tool execution**.

**FastAPI sidecar (`:8001`)** owns the Agno run loop and nothing else. It holds
no credential at rest and never touches the database. It receives an agent's
config over HTTP, lets the model choose tool calls, dispatches each one back to
Frappe, and streams SSE frames straight to the browser.

The stated invariant is:

> FastAPI never reads or writes the Frappe database directly, and never holds a
> credential at rest. It orchestrates; Frappe authorizes.

**This invariant holds on every path except MCP** (§4, F-4).

Authentication between the two is a short-lived HMAC run token (SHA-256, 300s
TTL, binding run/session/user/expiry, compared with `hmac.compare_digest`) plus a
service shared secret in `site_config.json`, passed as `X-Frappe-AI-Service-Secret`
rather than `Authorization` because Frappe intercepts Bearer tokens globally. This
is a correct and well-reasoned design.

Persistence follows [ADR 0017](../decisions/0017-mariadb-authoritative-lancedb-disposable.md):
MariaDB is authoritative, LanceDB is a disposable, rebuildable index, site-scoped
at `sites/<site>/private/files/lancedb`. Site isolation is correct here.

---

## 3. Frappe Assistant Integration

The brief's premise was inverted, so the useful output is a responsibility matrix
of what actually owns what.

| Concern | frappe_ai | frappe_assistant_core | Agno | Frappe |
|---|---|---|---|---|
| Conversation / session state | **Owns** | — | — | Storage |
| Run lifecycle + audit | **Owns** | — | — | Storage |
| Chat UI | **Owns** (React panel) | — | — | Desk host |
| Agent configuration | **Owns** | — | — | DocTypes |
| Tool *registry* | Bridges | **Owns** | — | — |
| Tool *execution* | Dispatch boundary | Tool bodies | — | **Permissions** |
| MCP endpoint | Client | **Owns** (server) | Client lib | — |
| LLM run loop | — | — | **Owns** | — |
| Streaming to browser | **Owns** (SSE) | — | Events | — |
| Permission enforcement | Calls into | Calls into | — | **Owns** |
| Knowledge / RAG | **Owns** | — | — | — |

**Real duplication is minimal.** `frappe_ai` does not reimplement Assistant Core's
tools; it consumes them through `dispatch_plugin_tool`, which runs them in-process
under the acting user. This is the correct arrangement and preserves the ADR 0003
invariant.

The one genuine overlap is **tool reachability by two routes**: an Assistant Core
tool can be invoked either directly (`dispatch_plugin_tool`, permission-scoped and
budgeted) or over MCP (in-process to FastAPI, connection-identity, unbudgeted).
Same tool, two very different security properties. [ADR 0019](../decisions/0019-mcp-acting-user-identity.md)
resolves this by making the direct path preferred and the MCP path an explicitly
documented, privileged-configuration exception.

Legacy `AI Tool` DocTypes are retained for compatibility during migration. That is
deliberate, not duplication — though it is the direct cause of F-8.

---

## 4. Agno Architecture Review

Verified against **agno 2.8.7 as installed**, by reading the package, not from
memory.

What the app does well: agents are constructed **per request** in
`service/builder.py`, never at module scope, so there is no cross-user agent
leak. `db=None` and `add_history_to_context=False` are deliberate — Frappe is the
only conversation store, and Agno's own session persistence is correctly declined
([ADR 0018](../decisions/0018-agno-usage-audit.md)). `telemetry=False` is set.
Each tool is wrapped as an Agno `Function` whose async entrypoint dispatches over
HTTP back to Frappe, which is exactly how ADR 0003's invariant is mechanised.

**Confirmed API facts** (checked against the installed signature):

- `Agent.__init__` accepts `tool_call_limit` and `tool_choice`.
- It does **not** accept `temperature`, `top_p`, or `max_iterations` — sampling
  belongs on the model.

This produced two findings. `AI Agent.max_iterations` was fetched into the run
config, documented in `002-feature-mapping.md` as enforced in the Agno loop, and
then **never passed to Agno** — the reasoning loop was bounded only by the model
choosing to stop asking for tools (F-6). `temperature`/`top_p` were likewise sent
and dropped (F-7). Both are now wired: `max_iterations` → `tool_call_limit`, and
sampling overlaid onto model params where the agent sets it.

**Deliberate divergences from Agno, all sound:**

- **Custom HITL instead of Agno's native `continue_run`/`active_requirements`.**
  Necessary, not stylistic: Agno's `Function.aexecute` swallows plain exceptions
  into `ToolExecution(tool_call_error=True)`, so a `PendingConfirmation` cannot
  propagate normally. The app recovers it via a marker string.
- **Custom memory and knowledge** rather than Agno's, because both must be
  permission- and site-scoped in ways Agno does not model.

**Fragility to pin.** Three couplings depend on Agno internals rather than public
API: the `fc` parameter injected by `_build_entrypoint_args`, the
`PENDING_CONFIRMATION_MARKER` string recovery, and `skip_entrypoint_processing`.
None is wrong, but all three will break silently on an Agno minor upgrade.
Regression tests pinning them are listed in §16.

No deprecated Agno APIs are in use.

---

## 5. Critical Production Issues

### F-1 — Authenticated remote code execution *(fixed)*

**Problem.** `AI MCP Connection` grants permissions to System Manager only. Two
whitelisted methods bypassed that DocType permission entirely.

**Evidence.** `frappe_ai/api/mcp.py`: `create_mcp_connection_from_json` had no
permission check and inserted with `ignore_permissions=True`, accepting a
caller-supplied `command`, `command_args` and `environment_variables`, with
`enabled` defaulting to `1`. `check_all_mcp_connections` had no permission check
and connected every enabled connection — which, for `connection_type: "stdio"`,
spawns `command` as a subprocess. `hooks.py` runs the second on
`*/5 * * * *`.

**Why it matters.** Any authenticated user of any role could create a connection
whose `command` is arbitrary, then either call the checker directly or simply
wait up to five minutes for the cron. Result: command execution as the `frappe`
OS user, which owns the site's private files, `site_config.json` (containing the
DB password and the service secret), and every other app on the bench.

**Production scenario.** A Website User with no desk access posts a single
whitelisted API call, waits five minutes, and reads `site_config.json`.

**Fix applied.** Both methods gated on `AI MCP Connection` permissions; insert
respects permissions; `get_mcp_health_dashboard` gated on read. Verified in
Frappe source that `has_permission` short-circuits for Administrator
(`frappe/permissions.py:104-106`) and `only_for` likewise (`frappe/__init__.py:535`),
so the cron and `after_migrate` paths are unaffected without needing split entry
points.

### F-2 — Budget bypass on the confirmation-approve path *(fixed)*

**Problem.** Execution budgets were not enforced for confirmation-gated tools.

**Evidence.** `service/routes/chat.py:_dispatch_approved` called
`frappe_client.dispatch_tool(...)` **without the `run` argument**;
`api/budgets.py:consume` began `if not run: return`.

**Why it matters.** Confirmation is required precisely on *mutating* tools. Every
tool call a human approved was therefore uncounted against `max_tool_calls`,
`max_mutations`, and `max_records_per_call` — the budget was absent from the one
path it most needed to cover.

**Fix applied.** `run` threaded through; `consume()` now fails closed, so any
future caller that omits a run is refused rather than silently unbounded.

### F-3 — Unprotected whitelisted methods *(fixed)*

`sync_fac_tools`, `run_ai_tool_migration`, `get_file_status`, and
`get_knowledge_base_inventory` had no permission checks.

More seriously, `AI Knowledge Source.resync` and `.reconcile` were reachable by
any user with **read**. Verified in Frappe source that `run_doc_method` enforces
only `doc.check_permission("read")` (`frappe/handler.py:280`) — whitelisting a
document method does *not* imply a write check. Both enqueue a full re-chunk and
re-embed of the source, so a read-only user could drive unbounded load against
the embedding service. Both now assert `write`.

### F-4 — MCP tools act as a shared identity *(decided)*

`service/builder.py:_build_mcp_tools` builds `MCPTools` that execute **in the
FastAPI process**, authenticating with a connection-level credential
(`Authorization: token {api_key}:{api_secret}`). There is no `frappe.set_user`
on this path because the call never re-enters Frappe's dispatch.

[ADR 0003](../decisions/0003-tools-execute-in-frappe.md) carved MCP out of its
invariant on the assumption MCP servers are foreign systems. In practice the MCP
targets here are Frappe — Assistant Core's own endpoint and same-bench app
servers — so those tools *do* touch Frappe data, as the connection rather than
the user. This is structurally the design ADR 0003 rejected, re-entered through
the door it left open.

Fixing F-1 changes the severity materially: this is no longer escalation by an
arbitrary user, but a System Manager deliberately configuring a shared-identity
integration. [ADR 0019](../decisions/0019-mcp-acting-user-identity.md) accepts
that as a documented limitation, bounded by three requirements: configuration is
privileged (done), same-site tools prefer the direct path, and MCP calls must
still be budgeted (F-11, open).

---

## 6. High-Priority Issues

### F-5 — Scheduled triggers had never run *(fixed)*

`dispatch_scheduled` enqueued `fire`, but `fire` threw for anything that was not
a DocType Event trigger. Worse, `last_fired_at` was advanced *before* the enqueue,
so each failure consumed its window permanently: the feature failed silently, for
every scheduled trigger, with no retry and no error surfaced to the user.

It survived because the only test asserted that `frappe.enqueue` was called and
never invoked `fire`.

Fixed, plus the window claim is now an atomic compare-and-swap — the previous
read-then-write let two schedulers both enqueue and run a data-mutating agent
twice for one window. The claim deliberately stays *before* execution: per
[ADR 0007](../decisions/0007-failure-over-durable-execution.md) a skipped run
beats a duplicated one.

### F-8 / F-9 — Rename fallout and the masking test suite *(fixed)*

`AI Agent Tool Config.tool` was renamed `tool_name` during the Assistant Core
migration. Three call sites were missed:

- `api/frontend.py` used `getattr(row, "tool", None)` — a **silent** failure that
  dropped every tool summary, so **every agent reported zero tools in the UI**.
  Confirmed against live bench data, not only fixtures.
- `api/api.py` used `row.tool` when resolving confirmation requirements.
- `AIAgent._snapshot` hardcoded `"tools": []`, emptying the tool list in every
  run's audit record.

The fixtures were never updated either, so **every test that created an agent
errored** — 62 of 366 tests broken, and the frontend bug sat directly behind that
wall. Restoring the fixtures made two previously-erroring tests start failing on
a real assertion, which is how the frontend bug surfaced.

Suite is now 373 tests with 11 broken, all pre-existing and environment-dependent
(7 from a Frappe core `ignore_user_permissions` change, also failing in `flow`'s
suite; 4 requiring Ollama or a clean site).

### F-10 — Knowledge retrieval performs no permission checks *(open)*

Verified: the entire `frappe_ai/knowledge/` module contains **no**
`frappe.has_permission` or `get_list` call. Retrieval is scoped to the agent's
configured knowledge bases and to nothing else.

Where a knowledge base was ingested from Frappe documents, a user can therefore
retrieve content from documents they cannot read, by asking the agent. This does
not require prompt injection — it is the normal behaviour of the feature. It is
the one place where the ADR 0003 invariant is broken *inside* Frappe rather than
at the MCP boundary.

Recommended fix: record source document identity on each chunk at ingestion, and
filter retrieved chunks through `has_permission` for the acting user before they
reach the model.

---

## 7. Maintainability / Architecture Findings

- **`service/routes/chat.py` is 860 lines** and carries the entire confirmation
  pause/resume state machine inline. Already tracked in
  [`to_do/high-chatpy-confirmation-extraction.md`](../to_do/high-chatpy-confirmation-extraction.md).
- **`hooks.py` registers `doc_events` on `"*"`** for five events, so every
  document write on the site enters `frappe_ai.triggers.dispatch` (F-14). There
  is an internal-DocType exclusion set, but the cost is paid site-wide on every
  write, including by apps with no AI involvement.
- **Error truncation limits are duplicated** at 500/300 across five files
  (pre-existing to-do).
- **No correlation id** ties a run's Frappe-side and FastAPI-side logs together —
  a two-process system's most basic observability requirement (pre-existing).
- **Documentation drift**: `002-feature-mapping.md` claimed `max_iterations` was
  enforced in the Agno loop. It was not. Specs must be reconciled (§17).

**Over-engineering found: very little.** This is worth stating plainly, since the
brief asked for removals as well as additions. The abstractions present are load-
bearing — the two-process split, the dispatch boundary, the budget layer, and the
custom HITL each solve a demonstrated problem. The one candidate for removal was
`AI Agent.temperature`/`top_p` as dead configuration; they were made functional
instead of deleted, because a field labelled "Temperature" silently doing nothing
is the worse failure and deletion would discard existing configured values.

---

## 8. Security Review

| Area | Assessment |
|---|---|
| Tool permission scoping (direct path) | **Correct.** `frappe.set_user(acting_user)` with restore in `finally`; permissions genuinely enforced. |
| Tool permission scoping (MCP path) | **Broken by design** — F-4, now documented and bounded (ADR 0019). |
| Knowledge retrieval | **Broken** — F-10, open. |
| Whitelisted method authorization | **Was broken** — F-1, F-3; now systematically checked. |
| Service authentication | **Correct.** HMAC run tokens, 300s TTL, `compare_digest`; shared secret in `site_config.json`, not the DB. |
| Secrets at rest | **Correct.** FastAPI holds no credential; secret is not in a DocType. |
| `safe_exec` sandboxing | **Correct.** Excludes `frappe.db.sql`, `frappe.qb`, `get_all`; forces acting user on `get_list`. |
| Prompt injection | **Bounded on the direct path** by design — injection cannot exceed the user's own permissions. **Not bounded** on MCP (F-4) or knowledge retrieval (F-10). |
| Log hygiene | **Weak** — F-13: `dispatch.py` writes full tool `arguments` to Error Log, and `fire_manual_trigger` writes full prompt context. Error Log is readable by roles far broader than the data may warrant. |
| LLM as authorization boundary | **Correctly avoided.** Authorization is always Frappe's, never the model's. |

The headline is that the security *model* is right and the failures were all
implementation gaps against it — which is the good failure mode, because each one
is a local fix rather than a redesign.

---

## 9. Concurrency Review

- **Agent construction is per-request**, not module-scope. No cross-user leakage.
- **`budgets.consume` is an unlocked read-modify-write** (F-12, open):
  `frappe.get_doc` → mutate counters → `db_set`. Two concurrent tool calls on the
  same run interleave and lose an increment, so a run can exceed its budget under
  exactly the parallel-tool-call conditions budgets are meant to bound. Needs
  `for update` row locking.
- **Scheduled trigger window claim** was a non-atomic read-then-write; now a
  compare-and-swap (F-5).
- **Trigger runs block an RQ worker** synchronously polling the FastAPI stream
  (pre-existing to-do). Under concurrent triggers this exhausts the worker pool
  with threads that are merely waiting.
- **LanceDB concurrent writes** are unguarded. Given ADR 0017 makes the index
  disposable the blast radius is bounded — a corrupt index is rebuildable — but a
  failed ingestion should not surface as a user-visible search outage.
- **Multi-site isolation** is correct: LanceDB paths are site-scoped, and no
  module-level state is keyed globally.

---

## 10. Database / Performance Review

- **Wildcard `doc_events`** (F-14) is the dominant systemic cost: five events on
  `"*"` means `frappe_ai.triggers.dispatch` is invoked on every write on the site.
  The dispatch does filter, but the filtering itself is the per-write cost, borne
  by every app on the bench.
- **`AI Run.budget_usage` is a JSON blob** read-modify-written per tool call —
  correct for audit, but it is the same row contended by every concurrent tool
  call in the run (F-12).
- **LanceDB reconnects on every knowledge operation** (pre-existing to-do).
- **Tool dispatch is an HTTP round-trip per call**, accepted in ADR 0003 as
  negligible against LLM latency. That reasoning still holds.
- **No N+1 patterns of consequence** were found in the run/session read paths.

---

## 11. Refactoring Plan

**Phase 1 — P0 security *(complete, `b25d09c`)*.** RCE, budget bypass,
whitelisted-method sweep, plus the field-rename fallout and test baseline needed
to verify any of it.

**Phase 2 — P1 correctness *(complete, `65e2fb9`)*.** Scheduled triggers, atomic
window claim, `max_iterations`, sampling.

**Phase 3 — P1 remaining *(open)*.** Knowledge permission filtering (F-10),
budget row locking (F-12), MCP budget enforcement (F-11), log hygiene (F-13).

**Phase 4 — P2 hardening *(open)*.** SSE heartbeats, non-blocking trigger runs,
LanceDB write guard, narrowing `doc_events`, correlation ids.

**Phase 5 — Documentation reconciliation *(open)*.** §17.

Phases 1 and 2 are independently deployable and carry no schema change.

---

## 12. Proposed Folder Structure

**No change proposed.** The current layout (`api/`, `service/`, `knowledge/`,
`memory/`, `tools/`, `triggers/`, `lib/`, `frappe_ai/doctype/`) maps cleanly onto
the architecture and the two-process split. Restructuring would create churn
without solving any finding in this review. The only file-level change worth
making is extracting the confirmation state machine out of `chat.py`, which is
already tracked.

## 13. Proposed Core Interfaces

**No new abstractions proposed.** Every finding above is a missing check, a
missing argument, or a missing lock inside an interface that already exists and
is correctly placed. Introducing new interfaces here would add indirection
without removing a defect — the dispatch boundary, the budget module, and the
builder are already the right seams.

The one interface change made was widening `_build_model` to accept `agent_cfg`,
so agent-level sampling has somewhere legitimate to land.

## 14. Files to Change

Already changed:

| File | Change |
|---|---|
| `api/mcp.py` | Permission gates; respect permissions on insert; fix command/args split |
| `api/budgets.py` | Fail closed on missing run |
| `api/dispatch.py` | *(unchanged; now correctly receives `run`)* |
| `api/fac_tools.py`, `api/migration.py` | Permission gates |
| `api/frontend.py`, `api/api.py` | `tool` → `tool_name` |
| `doctype/ai_agent/ai_agent.py` | Snapshot records real tool list |
| `doctype/ai_knowledge_source/…` | Permission gates; `write` on resync/reconcile |
| `service/routes/chat.py` | Thread `run` through `_dispatch_approved` |
| `service/builder.py` | `tool_call_limit`; agent sampling overlay |
| `triggers/triggers.py` | Accept scheduled triggers; atomic window claim |

Still to change: `knowledge/retriever.py` (permission filtering), `hooks.py`
(narrow `doc_events`), `api/dispatch.py` + `triggers/triggers.py` (log hygiene).

## 15. Files to Delete / Merge

**None recommended.** The legacy `AI Tool` / `AI Agent Tool` DocTypes are retained
deliberately for migration compatibility and still have live readers in
`api/migration.py` and `memory/memory.py`. Deleting them now would break the
migration path; they should be removed only after `medium-tool-migration-telemetry.md`
confirms legacy tool-call volume has reached zero.

## 16. Tests Required Before Production

Added in this branch:

- MCP connection creation and health checks refused for non-privileged users.
- Tool dispatch without a run is refused (budget fails closed).
- `max_tool_calls` actually stops the run at the limit — the budget path was
  previously only ever exercised with `consume` mocked out.
- `fire()` creates a run for a scheduled trigger.
- A due window can be claimed only once.
- `max_iterations` becomes Agno's `tool_call_limit`; agent sampling overrides
  model params.

Still required:

- Knowledge retrieval excludes chunks whose source document the user cannot read.
- Concurrent tool calls on one run cannot exceed `max_tool_calls` (F-12).
- MCP tool calls appear in `budget_usage` (F-11).
- **Agno coupling regression tests** (§4): that `fc` is injected into the
  entrypoint, that `PendingConfirmation` survives `Function.aexecute`, and that
  `skip_entrypoint_processing` behaves as assumed. These are the tests that will
  catch an Agno upgrade breaking HITL silently.
- Restore the 7 `ignore_user_permissions` tests against current Frappe.

## 17. Production Checklist

- [x] No unauthenticated or under-authenticated privileged endpoints
- [x] Execution budgets enforced on every in-Frappe tool path
- [x] Scheduled triggers verified to actually execute
- [x] Agent execution limits reach the model loop
- [x] Test suite able to exercise its own integration paths
- [ ] Knowledge retrieval permission-filtered (F-10)
- [ ] Budget accounting safe under concurrent tool calls (F-12)
- [ ] MCP calls budgeted (F-11)
- [ ] Tool arguments and prompt context kept out of Error Log (F-13)
- [ ] Correlation id across both processes
- [ ] SSE heartbeats for long runs behind proxies
- [ ] Specs reconciled: `002-feature-mapping.md` (`max_iterations`),
      `001-architecture.md` (MCP identity boundary per ADR 0019)
- [ ] `auto_approve` triggers remain disabled against production data until
      F-10, F-11 and F-12 close

---

## 18. Final Verdict

> **If you were responsible for operating this application for thousands of
> production users, would you deploy the current codebase as-is?**

**NO** — for the codebase as it stood at review start. The P0 remediation in this
branch removes the reasons that made that answer unambiguous, but two of the five
reasons below remain open.

1. **Authenticated remote code execution.** Any user of any role could obtain
   command execution as the `frappe` OS user, with a cron completing the chain
   unattended. Disqualifying on its own. *(Now fixed.)*
2. **The safety mechanisms were not actually running.** Budgets were bypassed on
   the mutating path, and `max_iterations` never reached Agno. The controls the
   architecture documents as its backstops were partly decorative. *(Now fixed.)*
3. **The test suite could not detect its own regressions.** 62 of 366 tests
   broken, agent creation impossible, and two real production bugs hiding behind
   that. A suite in this state provides no deployment confidence. *(Now fixed.)*
4. **Knowledge retrieval ignores permissions entirely.** Users can read document
   content they have no rights to, through normal use of the feature, with no
   injection required. **Still open.**
5. **Budget accounting is not concurrency-safe**, and MCP calls are not counted at
   all — so the remaining ceiling is unreliable exactly when it matters. **Still
   open.**

> **The smallest set of changes required before I would approve production
> deployment is:**

1. **F-1, F-2, F-3 — the P0 security fixes.** Done in `b25d09c`.
2. **F-5, F-6 — scheduled triggers and `max_iterations`.** Done in `65e2fb9`.
3. **F-10 — permission-filter knowledge retrieval.** Non-negotiable: it is
   unauthorized data disclosure through intended functionality.
4. **F-12 — row-lock `budgets.consume`.** Small change; without it the budget is
   advisory under concurrency.
5. **F-11 — count MCP tool calls against the budget**, or disable MCP connections
   in production until it lands. With ADR 0019 accepting a shared identity on that
   path, the volume ceiling is the only remaining control on it.
6. **Keep `auto_approve` triggers off production data** until 3–5 are closed —
   they remove the human backstop, and these are the machine backstops.

Everything else in this review — correlation ids, `chat.py` extraction, the
wildcard `doc_events`, SSE heartbeats, LanceDB reconnects — is real work that
improves operability, but none of it should block a deployment.

**Assessment.** This is a fundamentally sound system with good architectural
judgement behind it, whose defects clustered in the gap between what the
documentation asserted and what the code enforced. The ADRs were, in almost every
case, right; the code had drifted from them and the tests had stopped noticing.
Closing items 3–5 above makes it deployable.
