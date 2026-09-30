# Architecture Review — frappe_ai (2026-09-30)

**Scope and method.** Static review of the repository at the current head. I read in full: `service/{main,auth,config,frappe_client,builder}.py`, `api/{dispatch,budgets}.py`, most of `service/routes/chat.py` and `api/service.py`, the persistence callbacks in `api/api.py`, `utils/safe_exec.py`, `lib/model.py` (client construction), `knowledge/retriever.py`, `triggers/triggers.py` (dispatch and run path), `pyproject.toml`, pre-commit config, and the ADR/spec index. I skimmed the rest (frontend, `extract.py`, `ingest.py`, `memory/`, the DocType JSON). **Nothing was executed**: no tests, no service, no bench.

Evidence tags: **[V]** verified in source · **[I]** inference from source/docs · **[R]** risk that needs validation on a running system.

---

## 1. Executive Summary

The architecture is coherent and unusually well documented for its stage. The core idea, *Frappe authorizes, a stateless FastAPI+Agno service orchestrates*, is sound and clearly explained by 16 ADRs. Boundaries between config/persistence (Frappe), orchestration (FastAPI), and derived vector indexes (LanceDB) are clean, and the transcript-in-Frappe design makes the service genuinely stateless.

It is **not production-ready**, which the docs themselves say (Phase 8.1). My review agrees and adds findings the docs do not mention. The most important:

1. **The security boundary is the shared secret, not the user** (ADR 0003's guarantee is overstated). `dispatch_tool`/`dispatch_plugin_tool` accept `user` and `run` from the caller and do not verify that the run is active and owned by that user; budgets and run-scoping are skipped when `run` is omitted. Anyone holding the secret can act as any user, including Administrator. [V] `api/dispatch.py`
2. **Human-in-the-loop confirmation is enforced only inside the service process** (`builder._build_tool`), while dispatch "always executes what it's asked". Confirmation is a UX control, not a security control. [V]
3. **Stdio MCP connections spawn arbitrary commands inside the service process**, from DocType-configured `command`/`args`/`env`, and bypass Frappe permissions and budgets. [V] `service/builder.py::_build_mcp_tools`
4. **Trigger runs block a Frappe RQ worker for the entire LLM run**, which is exactly the problem ADR 0001 exists to solve. [V] `triggers.py::_run_via_service` (`requests.post(..., stream=True)` consumed synchronously)
5. **A real bug:** `service_health` references undefined `agent_doc`/`user` and would raise `NameError` whenever `service_base_url` is unset. [V] `api/service.py`
6. **Reliability primitives are thin:** no per-request connection pooling or retry in `FrappeClient`, no rate limiting, no idempotency on tool dispatch, non-atomic budget accounting, the runtime budget is measured from run *creation* (breaks approvals after pause).
7. **No AI evaluation, no tracing/metrics, no CI.** Tests are unit/mock oriented (~370 test functions, a large share on knowledge). No `.github/`, no eval harness, no prompt versioning. [V]

Maturity: solid **design and documentation (B+)**, **functional prototype implementation (B-)**, **production hardening (D)**.

---

## 2. Architecture Assessment

**Components [V]**

| Component | Location | Role |
|---|---|---|
| Frappe app | `frappe_ai/frappe_ai/doctype/*`, `api/*` | 22 DocTypes = config, sessions, runs, audit; whitelisted API; permission enforcement |
| FastAPI service | `frappe_ai/service/*` | `POST /stream/{run}` SSE; builds an Agno `Agent` per turn; HMAC run-token verification |
| Model layer | `lib/model.py`, `lib/resolver.py` | One OpenAI-compatible transport (ADR 0014); litellm only for provider/model UX (ADR 0013) |
| Tools | `tools/builtins.py`, `assistant_tools/native.py`, `api/fac_tools.py`, `api/mcp.py` | Built-ins, Frappe Assistant Core (FAC) tools, remote/stdio MCP |
| Sandbox | `utils/safe_exec.py` | RestrictedPython namespace for the `execute` tool and trigger conditions |
| Knowledge | `knowledge/*` | Extract → chunk → embed (Ollama) → LanceDB; MariaDB is authoritative (ADR 0002, 0016) |
| Memory | `memory/*` | MariaDB doctype + LanceDB FTS |
| Triggers | `triggers/triggers.py` | `doc_events` and cron → enqueue → run via service |
| Frontend | `frontend/src`, `public/frappe_ai_panel` | React SPA, desk-panel and page hosts |

**Control flow (interactive) [V]:** browser → `start_run` (Frappe) creates `AI Run`, mints a 300 s HMAC token → browser calls `POST /stream/{run}` on FastAPI directly → service verifies token → `get_run_config` (Frappe) → Agno loop; each tool call → `dispatch_tool` (Frappe, `set_user`) → on end `persist_run_result` (Frappe, as Administrator) → SSE `done`.

**Data flow notes.** Frappe returns the *decrypted provider `api_key`* and MCP credentials to the service on every run inside `get_run_config` [V] (`_model_call_config`, `_resolve_agent_mcp_connections`). "No credentials at rest" holds; "no credentials in the service" does not. The service also receives `lancedb_path` in `get_service_config`, although it "has no direct LanceDB access" [V].

---

## 3. AI Engineering Assessment

| Area | Status | Evidence |
|---|---|---|
| Provider abstraction | Good. Single OpenAI-compatible transport, bounded retries (`_bounded_retries`), timeouts, normalized provider errors | `lib/model.py` [V] |
| Agent orchestration | Delegated to Agno; conversation state deliberately kept out of Agno (`db=None`, `add_history_to_context=False`) | `builder.py` [V] |
| Tool calling | Tool schemas from DocTypes; dispatch server-side. `max_iterations`/`temperature`/`top_p` are fetched but never applied (C14) [V] | `builder.py::build` |
| HITL confirmation | Custom pause/resume via exception + string marker, not Agno-native. Understandable, but fragile (see D3) | `builder.py`, `chat.py` [V] |
| Fallback | One retry on the system default model, only if no tool has completed; the code's own comment says this "rarely" triggers in observed failures | `chat.py` [V] |
| Context management | Session transcript rebuilt from Frappe each turn; per-model tool-result char budget (`single_tool_result_char_budget`); attachments demote to RAG when large | `dispatch.py`, `ai_session.py` [V/I] |
| RAG | Hybrid vector+FTS in LanceDB; fixed embedding model (dimension pinned via index metadata); KB-scoped search; no reranking, no relevance threshold, no citation contract [V] | `retriever.py`, `store.py` |
| Memory | Agent memory in doctype + FTS; tool-writable (`update_memory`). Memory content is injected into prompts, so it is an injection persistence vector [I] | `memory/memory.py` |
| Prompt management | Prompt = `AI Agent.instructions` text + `<agent_memory>` block; instructions copied into the session transcript at first turn. No versioning, no eval linkage. Trigger prompts use `frappe.render_template` over document data | `ai_session.py`, `triggers.py` [V] |
| Evals | Only configuration-time capability checks (ADR 0015, `connection_test.py`); no regression/quality evals for agent behaviour [V] | — |
| Observability | Agno diagnostics forwarded to Frappe *Error Log*; per-call token/latency lines built in `chat.py` but only logged on failure; `usage` stored on `AI Run`. No trace IDs, metrics, or OpenTelemetry [V] | `chat.py`, `ai_run.py` |
| Cost control | Per-run tool/mutation/record/runtime budgets (ADR 0008). No token or currency budget per user/agent/day; no rate limit on `start_run` [V] | `budgets.py`, `api/api.py` |
| Determinism | `temperature`/`top_p` configurable; `config_snapshot` stored on `AI Run` (good for reproducibility). No seed, no model-version pinning check [V/I] | |

---

## 4. Key Architectural Decisions

Rating: **Keep** / **Modify** / **Replace**.

| # | Decision | What it does | Strengths | Weaknesses / failure modes | Verdict |
|---|---|---|---|---|---|
| D1 | **Agno + FastAPI split** (ADR 0001) | Moves LLM waits out of gunicorn workers | Real concurrency gain for interactive chat | Undermined for triggers (blocks RQ worker); adds a second process to operate; single process reads one site's config | **Keep**, fix trigger path |
| D2 | **Tools execute in Frappe as the user** (ADR 0003) | `dispatch_*` → `frappe.set_user` | Permissions evaluated by Frappe, not re-implemented | Trust rests on the shared secret; `user`/`run` unbound (see C1). `allow_guest=True` endpoints with header auth | **Keep**, harden binding |
| D3 | **Custom confirmation via exception + string marker** | Entrypoint raises `PendingConfirmation`; run loop greps tool error strings for a prefix | Avoids Agno session `db`; multiple pending calls per turn | Coupled to Agno's internal error-to-string behaviour (`str(e)` marker); resume re-dispatches from stored args; not enforced server-side | **Modify**: enforce approval in Frappe (signed approval bound to run+call id+args hash) |
| D4 | **HMAC run token, stdlib** (`auth.py`) | 300 s token over run/session/user | Simple, constant-time compare, no JWT surface | Not single-use despite docstring ("single-run"): replayable for 300 s per run; same secret also authenticates service→Frappe, so one leak = both directions; no key rotation/`kid` | **Modify** (see C1/C6) |
| D5 | **Secret in `site_config.json`** (ADR 0011) | Service reads file directly | One source of truth, no manual export | Service must run on the same bench filesystem (blocks separate-host deploys); `_bench_root()` assumes `parents[4]` path layout | **Keep** for now; add env override for containerised deploys |
| D6 | **LanceDB local files, MariaDB authoritative** (ADR 0002) | Vectors derived and rebuildable | Rebuildable, simple | Local-disk index: multi-worker/multi-node writers, backups, and rebuild time at scale are unproven [R] | **Keep**; validate concurrency, plan a server-based store if multi-node |
| D7 | **Fixed Ollama embeddings** (ADR 0016) | One vector space | Avoids dimension drift | Hard dependency on an Ollama endpoint; no fallback provider; `embed_texts` failure blocks all retrieval | **Keep**; document SLO and health check |
| D8 | **One `safe_exec` namespace** (ADR 0006) | Permission-gated helper functions over RestrictedPython | Removes SQL/QB/`get_all` escapes; centralised | RestrictedPython is defence-in-depth, not a hard boundary; `frappe.sendmail`, `frappe.enqueue`, `frappe.call` are exposed; no CPU/memory/time limit visible in this module [R] | **Keep**, add resource limits (or run out-of-process) |
| D9 | **Budgets** (ADR 0008) | DB counters on `AI Run` | Bounds blast radius of a looping agent | Non-atomic read-modify-write; mutation classification is a hard-coded name set; runtime measured from `creation`; skipped when `run` is None; remote MCP not covered | **Modify** |
| D10 | **litellm for UX only + OpenAI-compatible transport** (ADR 0013/0014) | Provider list/model suggestions from litellm; execution via OpenAI SDK | Removes per-provider SDK coupling | A heavy dependency (`litellm<1.83.8` pinned) for autocomplete only; OpenAI-compat endpoints may lag native features (caching, thinking) | **Keep**; consider replacing litellm with a static provider table |
| D11 | **Frappe as run/audit store** | `AI Run/Session/Message` doctypes | Free permissions, reports, audit | Every persistence and diagnostic callback is an HTTP round trip and Error Log write; Error Log used as the tracing backend | **Modify** for observability |
| D12 | **FAC (Assistant Core) integration + MCP** | Tools from FAC registry / MCP servers | Reuse of existing tool ecosystem | Two tool systems plus legacy `AI Tool` compatibility rows; three dispatch paths | **Keep**, finish the retirement plan to one path |

---

## 5. Critical Issues

| ID | Sev | Issue | Evidence | Tag |
|---|---|---|---|---|
| C1 | High | **Dispatch is not bound to the run or user.** `user` is a free parameter; `run` optional; with `run=None`, `consume()` returns immediately (budgets off) and `_resolve_plugin_context` returns no agent/KB scope. No check that `run.status in (Running, Paused)` and `run.owner == user` before executing (only budgets check status, and only when `run` is set). | `api/dispatch.py` L~70–140, `budgets.py::consume` | V |
| C2 | High | **Confirmation not enforced server-side.** Dispatch executes anything it is sent; `auto_approve` and `approved_call_ids` are decided in the service. Compromised service/secret ⇒ destructive tool calls with no approval. | `dispatch.py` module docstring, `builder._build_tool` | V |
| C3 | High | **Stdio MCP = command execution in the service**, configured by DocType data (`command`, `command_args`, `environment_variables`). Anyone who can write `AI MCP Connection` gets code execution as the service user. Also MCP calls skip Frappe permissions and budgets. | `builder._build_mcp_tools` | V |
| C4 | High | **Trigger runs consume the SSE stream inside an RQ worker** with `requests.post(stream=True, timeout=stream_timeout)`, occupying a worker for up to 600 s+ per fire. `doc_events` on `"*"` can multiply this. | `triggers.py::_run_via_service` | V |
| C5 | High | **Trigger prompt injection surface:** `frappe.render_template(prompt_template, ctx)` inserts document field content into the prompt; triggers can have `auto_approve` and run with mutation-capable tools; runs are created with `ignore_permissions=True`. A user who can edit a watched document can steer an unattended agent. | `triggers.py::_create_and_run_trigger` | V (mechanism) / R (impact) |
| C6 | Med | **Secret reuse and replay.** One secret signs run tokens and authenticates all service→Frappe calls; tokens are replayable for 300 s; no rotation path; `persist_run_result`/`fail_run` run as Administrator and (I saw no state-transition guard in `apply_result`) can overwrite a run's output/status. | `auth.py`, `api/api.py`, `ai_run.py::apply_result` | V / R |
| C7 | Med | **Bug: `service_health` NameError** when `service_base_url` empty (`_resolve_agent_plugin_tools(agent_doc, user)` inside that branch; neither name defined). | `api/service.py` (in `service_health`) | V |
| C8 | Med | **Budget correctness.** (a) counters are read-modify-write without a lock, while Agno may execute tool calls concurrently; (b) `max_runtime_seconds` is compared against `doc.creation`, so a run resumed after a slow human approval fails with "runtime budget exceeded"; (c) mutation tools are identified by a hard-coded slug set. | `budgets.py`, `dispatch.py` | V |
| C9 | Med | **Knowledge ingestion bypasses per-document permissions.** DocType sources are read with `frappe.get_all` (no permission filter) and any user with KB access can retrieve that text. Access control is at KB level only. Worth verifying which user the ingest job runs as. | `ingest.py` L~167, `retriever.py` | V (call) / R (impact) |
| C10 | Med | **Service HTTP client and resilience:** new `httpx.AsyncClient` per call (no pooling/keep-alive), no retry/backoff or circuit breaker, non-idempotent `dispatch_tool` (a retry would double-mutate; a client timeout after a successful mutation is not detectable). `/health` calls Frappe on every probe. | `frappe_client.py`, `main.py` | V |
| C11 | Med | **Fragile Agno coupling:** `_run_agent_turn` monkey-patches `model.ainvoke_stream`, attaches a `logging.Handler` to Agno's global `"agno"` logger per run (concurrent runs cross-contaminate handlers and log lines), and `_AgnoDiagnosticHandler.emit` calls `asyncio.create_task` (raises if invoked off-loop). Agno is pinned `>=2.8,<3` only. | `chat.py` | V |
| C12 | Low | **No rate limiting / abuse control** on `start_run`; CORS is `allow_methods=*`, `allow_headers=*`, `allow_credentials=True` (origins are allow-listed, acceptable, but tighten). | `main.py`, `api/api.py` | V |
| C13 | Low | **Sensitive data in Error Log:** trigger runs write ~6 `log_error` entries per run including document context; failure diagnostics include raw provider error bodies. Error Log is being used as an audit/trace store. | `triggers.py`, `chat.py` | V |
| C14 | Med | **Agent settings silently ignored:** `max_iterations`, `temperature` and `top_p` are returned by `get_run_config` but never referenced anywhere under `service/` or `lib/model.py` (grep-verified), so the AI Agent form fields have no effect on execution and there is no iteration cap besides the tool-call budget. Also `docs/specifications/010-review-topics.md` lists token-limit fallback and default-model-on-create as not implemented. | `api/service.py` L232-234, `builder.py` | V |

---

## 6. Improvement Opportunities

- Replace Error-Log-as-telemetry with structured logs + OpenTelemetry (trace id = `AI Run` name; spans for model call, tool dispatch, persistence).
- Add an evaluation layer: golden conversations per agent, tool-call assertion tests, prompt-injection regression set, run against a stub model in CI and a real model nightly.
- Version prompts: snapshot `instructions` hash into `config_snapshot` and expose it in run inspection (partly there via `config_snapshot`).
- Add token/cost budgets per run and per user/day using the `usage` already recorded.
- Collapse the three tool paths (`AI Tool`, FAC, MCP) into one dispatch contract as the retirement plan intends.
- Add a real CI pipeline (`.github/workflows`): ruff, `bench run-tests`, frontend `tsc --noEmit` + build, dependency audit.
- Frontend has no tests and no `lint`/`typecheck` scripts in `package.json` [V].
- Lint config ignores `F401`, `E501`, `B904`, `B017` globally; `UP`/`B` rules are partly neutered. Enable per-file ignores instead.
- Trim ADR/spec drift: several docs still describe superseded designs (env-var secret, "no litellm").

---

## 7. Recommended Target Architecture

Keep the split; tighten the trust model and make the service a proper cell:

1. **Run-scoped capability tokens for the service→Frappe direction.** Frappe mints, per run, a token (`run`, `user`, `exp`, `jti`). The service presents *that*, not the global secret, on every callback. Frappe derives `user` from the token; the `user` request parameter disappears. Keep the global secret only for service bootstrap/health.
2. **Approval enforced in Frappe.** `requires_confirmation` tools are refused by `dispatch_*` unless the `AI Run` row records an approval for `(call_id, tool, args_hash)`. The service only *requests* pauses.
3. **One tool contract.** `dispatch(tool_ref, args)` resolves FAC/builtin/`AI Tool` inside Frappe; MCP either runs through a Frappe-mediated allow-listed proxy or is restricted to remote (`streamable-http`/SSE) transports with Frappe-side budget accounting. Remove stdio MCP from the service, or run it in a separate sandboxed worker.
4. **Async trigger execution.** Trigger fire = create run, then `POST /runs/{run}/start` with a *non-streaming* service mode (service persists via callback); RQ worker returns immediately; completion is observed from `AI Run.status`.
5. **Observability plane.** OpenTelemetry from service and Frappe, trace id = run name, metrics (run latency, tokens, tool error rate, budget-exceeded count), structured JSON logs.
6. **Eval plane.** Versioned prompt+tool-set fixtures, CI-runnable with a fake model, scheduled real-model regression.
7. **State transitions on `AI Run` as a small state machine** (Running→Paused/Completed/Failed/Stopped) with guards in `apply_result`/`mark_failed`.

---

## 8. Prioritized Roadmap

Complexity: S ≤ 1 day · M ≈ 2–5 days · L > 1 week.

### Immediate fixes

| Item | Problem | Evidence | Impact | Proposed solution | Files | Cx | Prereq |
|---|---|---|---|---|---|---|---|
| I1 | `service_health` NameError | C7 | Health check from Desk crashes when unconfigured | Delete the stray `plugin_tools = ...` line; add a test | `api/service.py` | S | — |
| I2 | Dispatch unbound to run/user | C1 | Secret holder can act as any user, bypass budgets | Make `run` required; load run, require status Running/Paused and `run.owner == user` (`assert_run_owner`) before `set_user`; reject otherwise | `api/dispatch.py`, `api/budgets.py` | S–M | — |
| I3 | Budget runtime from creation; racy counters | C8 | Approvals after pause fail; concurrent calls overshoot limits | Use last-resume timestamp; atomic `UPDATE ... SET` or `SELECT ... FOR UPDATE` | `budgets.py`, `ai_run.py` | S–M | — |
| I4 | Stdio MCP in service | C3 | Config-to-RCE path | Feature-flag stdio MCP off by default; require System Manager and an explicit command allow-list | `builder.py`, `ai_mcp_connection.py` | S | — |
| I5 | Run-state guards | C6 | Callback can rewrite finished runs | Reject `persist_run_result`/`fail_run` unless status ∈ {Running, Paused} | `ai_run.py`, `api/api.py` | S | — |
| I6 | Add CI | — | No automated checks | Workflow: ruff, ruff-format, frontend `tsc`+build, Frappe test job | `.github/workflows/*` | M | bench in CI |

### Short-term

| Item | Problem | Proposed solution | Files | Cx |
|---|---|---|---|---|
| S1 | Approvals only in service (C2) | Store approvals on `AI Run`; dispatch rejects unapproved confirmation tools | `dispatch.py`, `ai_run.py`, `builder.py`, `chat.py` | M |
| S2 | Trigger worker blocking (C4) | Non-streaming start endpoint + poll/callback | `triggers.py`, `service/main.py` | M |
| S3 | Service client resilience (C10) | Shared `httpx.AsyncClient`, retries with backoff on idempotent GET/persist only, idempotency keys on dispatch (`run_id:call_id`) with Frappe-side dedupe | `frappe_client.py`, `dispatch.py` | M |
| S4 | Agno coupling (C11) | Replace the global-logger handler with per-run logger adapter; drop `ainvoke_stream` monkey-patch in favour of timeout config; add contract tests pinned to Agno version | `chat.py` | M |
| S5 | Prompt-injection controls (C5, memory, RAG) | Delimit and label untrusted content (`<untrusted_document>`), disable `auto_approve` for triggers that read user-editable fields by default, cap tool set for unattended runs | `triggers.py`, `ai_session.py`, `memory.py` | M |
| S6 | Ingestion permissions (C9) | Decide policy: ingest as a dedicated user and document that KB = ACL, or filter at retrieval by `has_permission` on `reference_*` | `ingest.py`, `retriever.py` | M |

### Medium-term

| Item | Proposed solution | Cx |
|---|---|---|
| M1 | Run-scoped capability tokens replacing per-call `user` param (target §7.1) | M–L |
| M2 | OpenTelemetry + metrics; stop using Error Log for traces | L |
| M3 | Eval harness (golden runs, injection set) in CI | L |
| M4 | Token/cost budgets and per-user rate limits on `start_run` | M |
| M5 | Consolidate `AI Tool`/FAC/MCP into one dispatch contract; finish retirement plan | L |
| M6 | Resource limits for `safe_exec` (timeout/memory, or subprocess) | M |

### Long-term

| Item | Proposed solution | Cx |
|---|---|---|
| L1 | Multi-site/multi-node service (per-site secret lookup, external secret store, HA) | L |
| L2 | Evaluate server-based vector store if LanceDB concurrency/backup becomes a constraint | L |
| L3 | Model routing (cost/latency/capability), context-window-aware fallback (spec 010 item 2) | L |

---

## 9. Final Engineering Checklist

**Production readiness**
- [ ] SSE heartbeats, bounded retries, rate limiting (Phase 8.1)
- [ ] Health/readiness distinguish "up" from "Frappe reachable"
- [ ] Graceful shutdown of in-flight runs marks them Failed/Paused
- [ ] Separate-host deployment supported (no `site_config.json` filesystem coupling)

**AI reliability**
- [ ] Fallback works after tool execution (or is explicitly disabled)
- [ ] `max_iterations` verified as enforced
- [ ] Context-limit handling with automatic summarisation/fallback
- [ ] Deterministic eval suite for each shipped agent

**Security**
- [ ] Dispatch bound to run owner/status; `run` mandatory
- [ ] Approvals enforced in Frappe
- [ ] Stdio MCP disabled or sandboxed
- [ ] Run-scoped tokens, secret rotation, single-use `jti`
- [ ] Untrusted content delimiting; unattended runs least-privilege
- [ ] Ingestion respects document permissions (or documented as KB-level ACL)
- [ ] No credentials or document content in Error Log

**Observability**
- [ ] Trace id per run across Frappe and service
- [ ] Token, latency, tool-error, budget metrics
- [ ] Structured logs; no per-request `log_error` churn

**Testing**
- [x] Substantial unit tests (auth, builder, chat route, budgets-adjacent, knowledge, safe_exec)
- [ ] CI pipeline running them
- [ ] Integration test: full run through a fake model + real dispatch
- [ ] Concurrency tests (parallel tool calls vs budgets; parallel LanceDB writers)
- [ ] Frontend type-check and component tests

**Scalability**
- [ ] Trigger runs do not hold RQ workers
- [ ] Pooled HTTP clients; bounded concurrency in service
- [ ] Vector store validated under multi-worker writes

**Maintainability**
- [x] ADRs for major decisions (now pruned to those in force)
- [ ] One tool-dispatch path
- [ ] Ruff ignore list reduced; per-file exceptions only
- [ ] README describes architecture and links to `docs/`

---

### What I could not verify

Runtime behaviour (latency, concurrency limits, LanceDB behaviour under parallel writers), which user the knowledge-ingest job runs as, whether Frappe Cloud/site config exposes the secret to other apps, the contents of the frontend beyond its file list, and the actual pass/fail state of the test suite.
