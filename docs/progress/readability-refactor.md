# Readability refactor progress

## Status

- Overall: Phases 0–4 complete; Phase 5 code and automated gates complete, live UI smoke pending; Phase 6 pending.
- Current phase: Phase 5 implementation completed 2026-10-03; live UI smoke pending.
- Starting commit: `6528fb3c142feac4b4f6c6dd104020fea1594603` on `final-cleanup-codex`.
- Scope: `apps/frappe_ai` only, a separate clean Git repository at the start of Phase 0. The dirty `apps/tender_automation` repository is outside this work.
- Completed: starting commit and public contracts inventoried; old Flow references classified; full `tact.local` Python and TypeScript baselines captured with no application code changes at the Phase 0 gate. The subsequent repair pass is recorded below.
- In progress: Phase 5 live UI smoke gate.
- Blockers: No browser runner or reachable local frontend in this workspace for the manual smoke gate. Original failures and TypeScript errors remain recorded below as the immutable pre-repair baseline.
- Remaining: Phase 5 live UI smoke, then Phase 6.

## Phase 0 contract inventory

Source of truth: `docs/specifications/005-frontend-contract.md`, `docs/specifications/001-architecture.md` §8, and the current implementation. The entries below are the public surfaces to compare in later phases.

### Frappe whitelisted methods

Each name below is a dotted method path under `frappe_ai.api.<module>.<name>`. All seven modules in the plan are present; `api/budgets.py` and `api/_service_url.py` have no whitelisted methods.

| Module | Methods |
| --- | --- |
| `api.api` | `start_run`, `resume_run`, `stop_run`, `recover_session`, `submit_feedback`, `get_agent_tools`, `attach_file`, `persist_run_result`, `fail_run`, `log_diagnostic` |
| `api.frontend` | `bootstrap`, `sessions`, `session_detail`, `start_run`, `resume_run`, `stop_run`, `recover_session`, `submit_feedback`, `upload_attachment`, `agent_tools`, `run_feedback` |
| `api.service` | `mint_token`, `service_health`, `get_service_config`, `get_run_config`, `get_fallback_model_config` |
| `api.dispatch` | `dispatch_tool`, `dispatch_plugin_tool` |
| `api.mcp` | `check_connection`, `check_all_mcp_connections`, `get_mcp_health_dashboard`, `get_mcp_connection_tools`, `create_mcp_connection_from_json` |
| `api.fac_tools` | `sync_fac_tools` |
| `api.migration` | `run_ai_tool_migration` |

`api.frontend` carries the 11 JSON methods documented in `005-frontend-contract.md`. `api.api` also preserves the older browser/run methods and service persistence callbacks. The service config and dispatch methods are separately callable service surfaces.

### Hooks, FastAPI, SSE, callbacks, and DocTypes

- `hooks.py`: `doc_events["*"]` maps `after_insert`, `on_update`, `on_submit`, `on_cancel`, and `on_trash` to `frappe_ai.triggers.dispatch`. Cron `*/5 * * * *` calls `frappe_ai.triggers.dispatch_scheduled` and `frappe_ai.api.mcp.check_all_mcp_connections`; daily scheduling calls `frappe_ai.knowledge.ingest.sync_due_sources`. `app_include_js` and `app_include_css` load versioned files under `public/frappe_ai_panel/`.
- `after_migrate` currently has **four** entries: `frappe_ai.assistant_tools.sync.sync_decorated_tools`, `frappe_ai.tools.builtins.sync_builtin_tools`, `frappe_ai.api.fac_tools.sync_fac_tools`, `frappe_ai.api.migration.migrate_ai_tools`. The plan's “trio” wording is stale; preserve all four.
- `service/main.py`: unauthenticated `GET /health`; authenticated `POST /stream/{run}` verifies a bearer run token and delegates to `frappe_ai.service.routes.chat.stream_chat`, returning `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- `service/routes/agno_events.py`: `_frame` emits `event: <type>` plus a JSON `data:` frame containing `type` and payload. The six event types are `run_started` (`run`, `session`), `text` (`content`), `tool_started` (`id`, `name`, `arguments`), `tool_ended` (`id`, `name`, `result`), `error` (`message`, with optional diagnostic fields), and `done` (`status`, `iterations`, `output`, `usage`, optional `questions`). `_done_payload` builds the `done` payload. The `id` fields on tool events are implemented additions to the abbreviated table in architecture §8 and match the frontend contract. `service/routes/chat.py` remains the `stream_chat` orchestrator.
- `service/frappe_client.py` callback paths: `frappe_ai.api.service.get_service_config`, `get_run_config`, `get_fallback_model_config`; `frappe_ai.api.dispatch.dispatch_tool`, `dispatch_plugin_tool`; `frappe_ai.api.api.persist_run_result`, `fail_run`, `log_diagnostic`.
- DocType JSON: 22 files under `frappe_ai/frappe_ai/doctype/`: `ai_agent`, `ai_agent_knowledge_base`, `ai_agent_mcp_connection`, `ai_agent_memory`, `ai_agent_plugin_tool`, `ai_agent_tool`, `ai_agent_tool_config`, `ai_fac_tool`, `ai_knowledge_base`, `ai_knowledge_chunk`, `ai_knowledge_source`, `ai_mcp_connection`, `ai_mcp_tool`, `ai_model`, `ai_provider`, `ai_run`, `ai_session`, `ai_session_attachment`, `ai_session_message`, `ai_settings`, `ai_tool`, `ai_trigger`. No DocType or field renames are planned.

## Old-Flow and phase narrative inventory

Scan command, run from this repository: `rg -n -i 'flow|ported from|vue panel|phase [0-9]' frappe_ai/ --glob '*.py'`. There are 107 matching lines in 41 Python files. The app-history/phase-narrative list below contains 94 lines in 37 files; the 13 benign matches in five files are listed separately. `pyproject.toml:30` also says “same pins as apps/flow”. Line numbers are the Phase 0 baseline for Phase 1's review, not a fixed future file count.

### App-history or phase narratives to review in Phase 1

```text
frappe_ai/api/api.py: 4, 6, 7, 8, 27, 286, 409
frappe_ai/api/dispatch.py: 6, 38
frappe_ai/api/service.py: 8, 11, 15, 54, 166
frappe_ai/frappe_ai/doctype/ai_agent/ai_agent.py: 4, 5, 7, 13
frappe_ai/frappe_ai/doctype/ai_knowledge_base/ai_knowledge_base.py: 4, 5, 7
frappe_ai/frappe_ai/doctype/ai_knowledge_chunk/ai_knowledge_chunk.py: 4, 5
frappe_ai/frappe_ai/doctype/ai_knowledge_source/ai_knowledge_source.py: 4, 5
frappe_ai/frappe_ai/doctype/ai_model/ai_model.py: 67
frappe_ai/frappe_ai/doctype/ai_run/ai_run.py: 4, 5, 23
frappe_ai/frappe_ai/doctype/ai_session/ai_session.py: 4, 5, 7, 8, 11, 15, 34
frappe_ai/frappe_ai/doctype/ai_session_attachment/ai_session_attachment.py: 4, 5
frappe_ai/frappe_ai/doctype/ai_tool/ai_tool.py: 4, 6, 8, 9
frappe_ai/hooks.py: 211, 212
frappe_ai/knowledge/attachment_store.py: 12, 13
frappe_ai/knowledge/chunker.py: 10
frappe_ai/knowledge/extract.py: 6, 7
frappe_ai/knowledge/ingest.py: 15, 16
frappe_ai/knowledge/knowledge.py: 10, 11
frappe_ai/knowledge/retriever.py: 27
frappe_ai/knowledge/store.py: 13
frappe_ai/lib/resolver.py: 6, 8
frappe_ai/lib/tool.py: 6
frappe_ai/service/auth.py: 11, 12
frappe_ai/service/builder.py: 6, 7, 27, 81
frappe_ai/service/frappe_client.py: 18, 21
frappe_ai/service/main.py: 6, 7, 8, 9, 78
frappe_ai/service/routes/chat.py: 4, 7, 13, 21, 265, 280, 599, 824
frappe_ai/tests/doctype/ai_model/test_ai_model.py: 81, 244
frappe_ai/tests/test_agent.py: 92
frappe_ai/tests/test_knowledge.py: 505, 1402, 1404
frappe_ai/tests/test_resolver.py: 202
frappe_ai/tests/test_safe_exec.py: 25
frappe_ai/tests/test_service_api.py: 7
frappe_ai/tests/test_service_app.py: 8
frappe_ai/tests/test_session.py: 5
frappe_ai/tools/builtins.py: 4, 10, 12
frappe_ai/utils/safe_exec.py: 158
pyproject.toml: 30
```

### Benign matches to preserve

```text
frappe_ai/api/fac_tools.py: 21 (the `Workflow` category)
frappe_ai/tests/test_conditions.py: 29 (`control_flow` test name)
frappe_ai/tests/test_openai_transport.py: 196 (`completion_flows` test name)
frappe_ai/tools/builtins.py: 245, 255, 260, 261, 263, 311, 312, 314, 431 (Frappe workflow behavior)
frappe_ai/utils/conditions.py: 73 (Python control flow)
```

## Phase 0 baseline gates

### Python

- Command: `env/bin/bench --site tact.local run-tests --app frappe_ai` from the bench root.
- First sandboxed attempt: stopped before test discovery because the sandbox denied the database TCP socket (`frappe.testing.discovery.TestRunnerError: Failed to connect to the database: (2004, "Can't create TCP/IP socket (1)")`). Log: `/tmp/frappe-ai-phase0-tests-2026-10-03.log`. This is an environment failure, not a test failure.
- Rerun with database access: completed, exit 1. Log: `/tmp/frappe-ai-phase0-tests-2026-10-03-escalated.log`; Frappe detailed log: `/home/a/harsha/harsha/logs/frappe.testing.log`. Results: 10 unit tests passed; 416 integration tests ran with 2 failures and 7 errors; 66 unspecified-category tests ran with 1 failure and 1 error. Total: 492 tests, 3 failures, 8 errors. These are the starting-state results, recorded without code fixes.
- Read-only site-state check after the test run: 49 `AI Run` rows have `status = "Running"`; the oldest `modified` timestamp is `2026-08-06 19:18:22.627559`. No cleanup was performed. This is the observed post-baseline state on `tact.local`, not an assertion that the test run created any of those rows.

Failing integration tests:

| Test | Result observed |
| --- | --- |
| `test_safe_exec.TestFrappeAISafeExec.test_count_returns_number` | Error: `DatabaseQuery.execute()` rejects `ignore_user_permissions`. |
| `test_safe_exec.TestFrappeAISafeExec.test_db_get_list_ignore_permissions_is_stripped` | Same error. |
| `test_safe_exec.TestFrappeAISafeExec.test_get_list_ignore_permissions_is_stripped` | Same error. |
| `test_safe_exec.TestFrappeAISafeExec.test_get_list_ignore_user_permissions_is_stripped` | Same error. |
| `test_safe_exec.TestFrappeAISafeExec.test_get_list_supports_aggregates` | Same error. |
| `test_safe_exec.TestFrappeAISafeExec.test_get_list_user_impersonation_is_stripped` | Same error. |
| `test_conditions.TestEvaluateCondition.test_condition_can_query_db` | Same error. |
| `test_knowledge.TestIngest.test_empty_source_completes_with_zero_chunks` | Failure: source status `Failed`, expected `Completed`. |
| `doctype.ai_model.test_ai_model.TestAIModelDefault.test_get_default_model_returns_none_when_none_set` | Failure: returned `Minimax M3`, expected `None`. |

Failing unspecified-category tests:

| Test | Result observed |
| --- | --- |
| `test_service_auth.TestRunTokenTampering.test_tampered_user_rejected` | Error: test's token split has no index 2 (`IndexError`). |
| `test_chat_route.TestStreamChatProviderAndConfirmation.test_provider_failure_is_normalized_in_sse_and_persistence` | Failure: event code `provider_error`, expected `rate_limit`; the test client's `SimpleNamespace` lacks `get_fallback_model_config`. |

### Frontend TypeScript

- `frontend/package.json` declares `lucide-react`; `frontend/build.mjs` has the `factory_automation_api/fact-frontend/node_modules` fallback. Local `frontend/node_modules` lacked `react`, `react-dom`, `lucide-react`, and `typescript`; ignored symlinks to the existing fallback tree supplied them. No tracked dependency or application file changed.
- Command: `frontend/node_modules/typescript/bin/tsc --project frontend/tsconfig.json --noEmit` from the app repo. Log: `/tmp/frappe-ai-phase0-tsc-2026-10-03.log`.
- Baseline result: exit 2 with **two errors**, as expected: `frontend/src/components/WorkspaceShell.tsx(48,75) TS2339` accesses `executions` on the `TranscriptMessage` union; `frontend/src/state/store.tsx(472,7) TS2322` assigns a `string` from `executionStatusFromResult` in `frontend/src/lib/toolMeta.ts` to the narrower `approval_status` union. Record these as pre-existing Phase 0 issues; Phase 5 owns fixes.

## Phase tracking

| Phase | Status | Gate / next action |
| --- | --- | --- |
| 0 — contracts and baseline | Complete | Contracts and both baselines were recorded before application changes. |
| 1 — comments and docstrings | Complete | 37 Python files and the packaging comment updated; gates recorded below. |
| 2 — chat split | Complete | SSE and confirmation tests passed; module import graph is acyclic. |
| 3 — API seams | Complete | Focused tests and syntax gates passed. |
| 4 — triggers and knowledge | Complete | Full 499-test gate passed. |
| 5 — frontend | Code complete; live UI smoke pending | Typecheck, six event tests, build, and full 499-test app suite passed. |
| 6 — integration | Pending | Compare full gates against this baseline. |

## User-requested post-baseline repairs (2026-10-03)

These changes followed the completed Phase 0 gate after the user asked to fix the recorded failures. They do not change the baseline results above or mark Phases 1–6 complete.

- `frappe_ai/utils/safe_exec.py`: omit unsupported `ignore_user_permissions` when calling this Frappe version's `get_list`; continue stripping caller-supplied bypass flags and pinning the current user. Use the current string aggregate field syntax for the permissioned count helper. `test_safe_exec.py` now tests that syntax.
- `test_service_auth.py`: decode and re-encode the opaque base64 token before changing a signed field, so tampering tests exercise signature verification.
- `test_chat_route.py`: give the fake Frappe client the current fallback-config and diagnostic callbacks; the provider error assertion now exercises the intended path.
- `test_knowledge.py`: assert the existing deliberate `Failed` status and error text for an empty non-DocType source. No ingestion behavior changed.
- `test_ai_model.py`: clear any site-configured default within the test transaction before testing default selection. The real site default was preserved.
- `frontend/src/components/WorkspaceShell.tsx` and `frontend/src/lib/toolMeta.ts`: narrow the transcript role before accessing `executions` and type the parsed approval status as its actual union. Regenerated `frappe_ai/public/frappe_ai_panel/frappe_ai_panel.js` from the changed frontend source.

Verification: `env/bin/bench --site tact.local run-tests --app frappe_ai` passed all 492 tests (10 unit, 416 integration, 66 unspecified-category). Log: `/tmp/frappe-ai-post-baseline-repair-tests-2026-10-03.log`. TypeScript (`tsc --project frontend/tsconfig.json --noEmit`) and `npm run build` passed. The five model-default tests passed again after the full suite. A read-only site check still found default model `Minimax M3` and 49 `Running` AI runs; no cleanup was performed.

## Phase 1 — comments and docstrings (2026-10-03)

- Rewrote the 94 inventoried app-history/phase-narrative lines across API, service, DocTypes, knowledge, tools, hooks, and tests as current contracts. Reworded the `pyproject.toml` Flow pin comment without changing dependency values. The benign `workflow`, `control_flow`, and `completion_flows` matches remain.
- Scope gate: compared the Phase 1 files with a snapshot taken immediately before this phase. After removing Python docstrings, the executable AST is identical in all 37 changed Python files; `tomllib` parses identical `pyproject.toml` values. `git diff --ignore-space-change --check` passed. A raw repository `git diff` still includes the separately recorded post-baseline repairs, so it is not a Phase 1-only diff.
- Syntax gate: `python -m compileall -q frappe_ai` passed.
- Flow gate: `rg -n -i 'flow|ported from|vue panel|phase [0-9]' frappe_ai/ --glob '*.py' pyproject.toml` found no genuine old-Flow or phase narrative references. Its remaining matches are the benign workflow/control-flow names and descriptions listed above.
- Focused test: the plan's suggested `-m test_chat_route` option is unsupported by this bench. The equivalent `env/bin/bench --site tact.local run-tests --app frappe_ai --module frappe_ai.tests.test_chat_route` passed all 5 tests.
- No Phase 2 route modules were created; `agno_events.py`, `transcript.py`, and `confirmation.py` remain pending.

### Docstring concision follow-up (2026-10-03)

- Audited all 135 Python files under `frappe_ai/` and shortened 88 explanatory docstrings across 26 files. API and service docstrings now state their contracts directly; security, confirmation, transcript, and failure invariants remain at the relevant code. The longer `tools.builtins.execute` docstring stays because it is the model-facing contract for allowed script behavior.
- Scope gate: compared the Python tree with a snapshot taken immediately before this follow-up. Executable ASTs are identical in all 26 edited files after removing docstrings. This isolates the docstring pass from the earlier post-baseline repairs and Phase 1 edits in the dirty working tree.
- Verification: `python -m compileall -q frappe_ai` and `git diff --check` passed. The Flow-reference scan still has only benign workflow/control-flow matches. `env/bin/bench --site tact.local run-tests --app frappe_ai --module frappe_ai.tests.test_chat_route` passed all 5 tests.

## Phase 2 — chat route split (2026-10-03)

- Extracted Agno turn execution and SSE framing into `service/routes/agno_events.py`, transcript conversion and serialization into `service/routes/transcript.py`, and confirmation handling into `service/routes/confirmation.py`. `chat.py` now contains `stream_chat` and its imports only. The import chain is `transcript` → `confirmation` → `agno_events` → `chat`; `chat` also imports transcript and confirmation directly.
- Placed `_reconstructed_tool_call_message` in `transcript.py` because `_to_agno_messages` calls it and confirmation result building calls transcript helpers. This resolves the import cycle in the plan's original ownership table. An AST comparison against the prior `chat.py` found all 25 original definitions and no changed function or class bodies.
- Updated `test_service_app.py` imports; `test_chat_route.py` still patches `chat.AgentBuilder`. Added denial and redirect transcript assertions and a completed-tool provider-failure test. `builder.py`, the Agno version range, migration hooks, legacy dispatch, and FAC compatibility were not changed.
- Phase 2 gate on `tact.local`: `test_chat_route` 7, `test_service_app` 11, `test_builder` 9, `test_openai_transport` 12, and `test_model` 14 tests passed. Full `env/bin/bench --site tact.local run-tests --app frappe_ai` passed 494 tests (10 unit, 416 integration, 68 unspecified-category). `python -m compileall -q frappe_ai` and `git diff --check` passed.

## Phase 3 — Frappe API seams (2026-10-03)

- Moved the 12 presentation-only helpers from `api/frontend.py` to `api/frontend_view.py`; kept database access, permission checks, bootstrap orchestration, and all whitelisted methods in `frontend.py`.
- Split `get_run_config` into private run-graph loading and payload assembly helpers. Service-secret verification, acting-user restoration, ownership checks, and the seven payload keys remain unchanged.
- Replaced both dispatch acting-user save/restore blocks with `_acting_user_context`, entered after authentication, run ownership, binding, and confirmation checks. Kept the three API modules' service-secret verifiers independent. Shortened only the `api.py` module docstring; all ten whitelisted paths remain.
- Added tests for identity restoration on service lookup and tool errors, service and plugin owner rejection, and FAC permission denial. Existing tests cover single-use approval and budget consumption.
- Phase 3 gate on `tact.local`: `test_api` 60, `test_service_api` 15, `test_budgets` 6, `test_mcp` 9, and `test_decorated_tools` 7 tests passed. `python -m compileall -q frappe_ai/api` and `git diff --check` passed.

## Phase 4 — Triggers, knowledge, tools, controllers (2026-10-03)

- Moved the service request, SSE terminal handling, error payload parsing, and terminal run polling unchanged from `triggers/triggers.py` to `triggers/service_runner.py`. Trigger dispatch, scheduling, event matching, run creation, and the four `triggers/__init__.py` exports remain in place.
- Updated `test_triggers.py` to import and patch the runner at its new module path. Clarified comments in the knowledge store, knowledge DocType controllers, agent memory, and FAC compatibility; no ingestion, search, tool migration, or authorization logic changed. Kept the separate 300-character builtin and 500-character dispatch error limits.
- Phase 4 gate on `tact.local`: `test_triggers` passed 14 tests; full `env/bin/bench --site tact.local run-tests --app frappe_ai` passed 499 tests (10 unit, 421 integration, 68 other). `python -m compileall -q frappe_ai` and `git diff --check` passed. Codebase-memory graph indexing was unavailable because its MCP tools were not exposed in this session.

## Phase 5 — React state and frontend transport (2026-10-03)

- Moved store data and context types to `frontend/src/state/types.ts`. Moved the normalized run-event switch and its transcript, question, and execution helpers to `frontend/src/state/runEvents.ts` as `applyRunEvent(state, event, messageId)`. `store.tsx` retains `SessionHostAdapter`, `StoreProvider`, `useStore`, fetching, React effects, scroll updates, and history refresh.
- Added a discriminated normalized-event union in `api/stream.ts`. The Frappe CSRF and FastAPI bearer POSTs, `run_started.run → name`, and `text.content → delta` remain intact. The two Phase 0 TypeScript errors were already fixed in the post-baseline repair pass; `executionStatusFromResult` now also declares its return type explicitly.
- Added six pure event tests with an esbuild bundle and Node `node:test`, using the existing fallback dependency path. The cases cover text accumulation, duplicate tool start, tool result error, paused questions, terminal error, and unknown events.
- Automated gates: `tsc --project frontend/tsconfig.json --noEmit` passed; `npm test` passed all six cases; `npm run build` passed; full `env/bin/bench --site tact.local run-tests --app frappe_ai` passed 499 tests (10 unit, 421 integration, 68 other); `git diff --check` passed. The changed frontend source regenerated `frappe_ai/public/frappe_ai_panel/frappe_ai_panel.js`; CSS is unchanged.
- Live smoke is pending: desk panel and standalone page bootstrap, session switch, stream, stop, pause/resume, attachments, and inspector. This workspace has no browser runner and no reachable local frontend (`tact.local:8000` DNS lookup and `127.0.0.1:8000` connection failed), so these interactions were not observed.
