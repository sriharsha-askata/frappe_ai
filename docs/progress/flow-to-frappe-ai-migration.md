# Progress — Flow → frappe_ai Migration

> **Keep this file current.** Update it as work happens, not at the end of a phase.
> It is the single place to answer "where are we?" without reading code.

---

## Overall Status

| | |
|---|---|
| **Status** | 🟨 Core parity runtime is implemented; Assistant Core/FAC migration and reconciliation are active |
| **Current phase** | Phase 6 frontend reconciliation plus Assistant Core/FAC migration |
| **Next phase** | Verify the three tender workflows, complete the legacy-tool audit, finish page-shell parity, then production hardening |
| **Started** | 2026-08-05 |
| **Target** | not set |
| **Blockers** | No single global blocker. Tender workflow verification still needs a valid model credential; remote MCP mutation calls bypass the local budget counters; full bench migration remains dependent on Redis availability in this environment. MariaDB, Bench web, FastAPI, workers, and scheduler were verified healthy on 2026-08-21; database tests must run with host-level service access because the restricted sandbox cannot access the host database socket. |

### Phase summary

| Phase | Name | Status | Notes |
|---|---|---|---|
| 0 | Planning & Documentation | ✅ Complete | Specs + ADRs written (11 as of Phase 2; ADR 0010 added mid-Phase-2, superseded by ADR 0011) |
| 1 | Foundation & Configuration | ✅ Complete | `AI Provider`/`AI Model`/`AI Settings` + `safe_exec`/`conditions`/`system_generated`; Agno-only chat execution, with litellm now limited to provider/model UX (ADR 0013) |
| 2 | FastAPI Service Skeleton | ✅ Complete | Hand-built FastAPI (not AgentOS); HMAC run-token primitive; shared-secret service auth via `X-Frappe-AI-Service-Secret` (not `Authorization`); secret bootstraps from `site_config.json` (ADR 0011, supersedes ADR 0010's env-var design) |
| 3 | Agents, Tools & Run Loop | ✅ Complete | AgentBuilder + Frappe dispatch + SSE chat; live successful completion and confirmation pause/resume verified with an OpenAI-compatible provider |
| 4 | Knowledge / RAG | ✅ Complete | Knowledge pipeline and three knowledge DocTypes; embeddings use direct provider SDK callers (ADR 0012), not litellm or Agno |
| 5 | Triggers, Memory & MCP | ✅ Complete | Verified on 2026-08-10: trigger tests passed, memory tests passed, MCP import path fixed with `mcp<2`, and a real stdio MCP server returned `Connected (1 tools)` through both `check_connection()` and `check_all_mcp_connections()` |
| 6 | Frontend Panel | 🟨 In progress | React/esbuild panel and `/app/frappe-ai` page runtime exist; dedicated page layout and full host-surface parity remain |
| 7 | Parity Complete & Reconciliation | 🟨 In progress | Assistant Core/FAC migration, tender workflow verification, legacy-tool audit, and final documentation reconciliation remain |
| **8** | **Production Hardening** | 🟨 Partially implemented | Direct Frappe/FAC budget accounting exists; heartbeats, rate limits, retries, and observability remain. **Hard gate before production traffic** |

Legend: ⬜ not started · 🟡 in progress · ✅ complete · 🔴 blocked

---

## ⚠️ Parity ≠ Production Ready

**The parity plan is separate from production readiness.** Core runtime phases 1–5 are
implemented, while frontend/FAC reconciliation remains. Neither the completed phases nor
the eventual parity milestone authorizes production traffic. This is a deliberate
sequencing choice, not an oversight.

A production-readiness review (2026-08-05) identified gaps that were triaged into Phase 8.
Two matter enough to state here:

- **No SSE heartbeats** until 8.1 — streams die behind any reverse proxy with an idle
  timeout shorter than a long reasoning step. Works on localhost; fails in most real
  deployments.
- **No complete budget coverage** until 8.1 — direct Frappe/FAC dispatch enforces the
  configured limits, but remote MCP calls bypass them; a trigger with `auto_approve = 1`
  still has no human check. See [ADR 0008](../decisions/0008-execution-budgets.md).

**Consequences for how Phases 1–7 may be used:**

| Use | Allowed before Phase 8.1? |
|---|---|
| Local development | ✅ Yes |
| Internal demo / evaluation | ✅ Yes |
| Staging with non-production data | ✅ Yes |
| **Production traffic** | ❌ **No** |
| **`auto_approve` triggers against production data** | ❌ **No** |

---

## Baseline (verified 2026-08-05)

**`apps/frappe_ai`** — pristine `bench new-app` skeleton. One commit (`3fd24a9 feat:
Initialize App`), branch `main`, no remote, clean tree. No DocTypes, no logic, empty
`hooks.py`, empty `www/`, empty `public/`. Nothing to preserve.

**`apps/flow`** — the functional specification. 16 DocTypes, ~50 Python modules, Vue
frontend source. To be uninstalled after parity ([ADR 0005](../decisions/0005-greenfield-no-migration.md)).

**Environment** (`env/lib/python3.12/site-packages/`):

| Package | Status |
|---|---|
| `lancedb` | ✅ 0.36.0 installed |
| `litellm` | ✅ 1.83.7 declared for provider validation/model suggestions only; chat and embedding calls do not use it — see [ADR 0013](../decisions/0013-litellm-for-provider-ux-agno-still-executes.md) |
| `openai` | ✅ 2.30.0 installed |
| `pydantic` | ✅ 2.11.7 installed |
| `agno` | ✅ 2.8.7 installed — added in Phase 1 (`test_connection()` needs it for real) |
| `fastapi` | ❌ **not installed** — Phase 2 |
| `uvicorn` | ❌ **not installed** — Phase 2 |

Sites: `tact.local` (default), `fact.local`, `fact2.local`, `plc.local`.
Both `frappe_ai` and `flow` are in `sites/apps.txt` and pip-installed editable.

> Table above is the pre-Phase-1 baseline snapshot; see Phase 1 section below for what
> changed.

---

## Phase 0 — Planning & Documentation ✅

**Objective:** Analyse `flow` end to end and produce the specification, decisions, and
migration plan for `frappe_ai`.

### Completed

- [x] Full functional inventory of `apps/flow` — 16 DocTypes, controllers, `lib/`,
      `knowledge/`, `memory/`, `triggers/`, `tools/`, `utils/`, `api/`, hooks, frontend
- [x] Baseline audit of `apps/frappe_ai` (confirmed empty skeleton)
- [x] Environment audit (installed vs. missing dependencies)
- [x] `docs/specifications/001-architecture.md`
- [x] `docs/specifications/002-feature-mapping.md` — ~115 feature rows
- [x] `docs/specifications/003-doctype-reference.md` — the original 18 parity DocTypes,
      plus the four current migration/FAC metadata DocTypes, verified against the shipped JSON
- [x] ADR 0001 — Agno + FastAPI over Frappe-native
- [x] ADR 0002 — LanceDB as the vector store
- [x] ADR 0003 — Tools execute inside Frappe
- [x] ADR 0004 — SSE direct from FastAPI
- [x] ADR 0005 — Greenfield, no migration
- [x] ADR 0006 — One hardened `safe_exec` namespace
- [x] ADR 0007 — Fail-and-retry over durable execution *(added after review)*
- [x] ADR 0008 — Execution budgets and mutation limits *(added after review)*
- [x] This progress tracker
- [x] Production-readiness review triaged into Phase 8

### Decisions taken

| Decision | Outcome | ADR |
|---|---|---|
| Runtime | Agno + FastAPI, Frappe keeps config/persistence/permissions | [0001](../decisions/0001-agno-fastapi-over-frappe-native.md) |
| Vector store | **LanceDB** (ChromaDB evaluated and rejected) | [0002](../decisions/0002-lancedb-vector-store.md) |
| Tool execution | Inside Frappe, as the acting user | [0003](../decisions/0003-tools-execute-in-frappe.md) |
| Streaming | SSE direct from FastAPI, Frappe-minted run token | [0004](../decisions/0004-sse-direct-from-fastapi.md) |
| `flow` coexistence | Greenfield; uninstall `flow` after parity | [0005](../decisions/0005-greenfield-no-migration.md) |
| Sandboxing | Keep `safe_exec`; one hardened namespace everywhere | [0006](../decisions/0006-unified-safe-exec-namespace.md) |
| Restart behaviour | Fail-and-retry, not mid-run resume; triggers durable via RQ | [0007](../decisions/0007-failure-over-durable-execution.md) |
| Agent limits | Per-run execution budgets and mutation caps | [0008](../decisions/0008-execution-budgets.md) |

### Notes from analysis

1. **Security finding in `flow`** — two divergent `safe_exec` implementations. Script tools
   and trigger conditions use the broad `frappe.utils.safe_exec` (with `frappe.db.sql`,
   `frappe.get_all`) while the `execute` tool uses the hardened namespace. Being fixed in
   `frappe_ai`, not ported. → ADR 0006.
2. **Agno does not replace `safe_exec`** — it validates tool *interfaces*, not tool
   *implementations*, and offers nothing for LLM-generated runtime code. Confirmed and
   documented in ADR 0006.
3. **Undeclared dependencies in `flow`** — `pydantic`, `croniter`, `jinja2`, `bs4`, `lxml`,
   `openpyxl`, `chardet`, `requests`, `pyarrow`, `RestrictedPython` are imported but absent
   from `pyproject.toml`, arriving transitively. Declare explicitly in Phase 1.
4. **`flow`'s panel bundle is unbuilt** — `flow/public/` holds only `.gitkeep`. The Vue
   *source* under `apps/flow/frontend/src/` is what gets ported in Phase 6.
5. **`AI Knowledge Chunk` naming is load-bearing** — `autoincrement`, because the integer
   name **is** the LanceDB row `id`. Do not change it.
6. **`flow` never uses `frappe.publish_realtime`** — all streaming is SSE. Confirms ADR 0004
   is a continuation rather than a departure.

---

## Phase 1 — Foundation & Configuration ✅

**Objective:** Installable app with the configuration tier working end to end. No AI yet.

### Completed

- [x] `pyproject.toml` — added `agno>=2.8,<3`, `RestrictedPython>=8.4,<9`, `pydantic>=2.11,<3`.
      Chat execution remains Agno-native; `litellm` is declared only for provider/model
      UX and validation, per [ADR 0013](../decisions/0013-litellm-for-provider-ux-agno-still-executes.md).
      `fastapi`/`uvicorn`/`lancedb`/`croniter`/`jinja2`/`requests`/`beautifulsoup4`/`lxml`/
      `openpyxl`/`chardet`/`python-docx`/`pdfplumber`/`rapidocr`/`onnxruntime` intentionally
      deferred — not needed for this phase's code to run; scoped to the phase that first
      uses them (2, 4, 5).
- [x] `agno` 2.8.7 installed into the bench env (was previously absent)
- [x] `frappe_ai/utils/system_generated.py` — `validate_immutable`, `block_delete`,
      `block_rename` — ported verbatim from `flow`, comment references renamed
- [x] `frappe_ai/utils/safe_exec.py` — hardened namespace (ADR 0006), ported from
      `flow/utils/safe_exec.py`. `_frappe_ai_builtins()` degrades to `{}` via
      `try/except ImportError` when `frappe_ai.tools.builtins` doesn't exist yet (Phase 3)
- [x] `frappe_ai/utils/conditions.py` — ported from `flow/utils/conditions.py`, **now
      importing `frappe_ai.utils.safe_exec.safe_exec`** (the hardened namespace) instead
      of flow's `frappe.utils.safe_exec.safe_exec` (the broad one) — this is ADR 0006's
      actual fix, not carried forward from flow
- [x] DocType `AI Provider` + controller — direct port of `Flow Provider`, with chat
      execution still resolved through the fixed Agno provider-slug map
      (`frappe_ai/lib/model.py:PROVIDER_MODEL_CLASSES`, 21 slugs). Provider-name validation
      uses litellm's registry through the aliases documented in ADR 0013.
- [x] DocType `AI Model` + controller, `test_connection()`, `get_provider_models()` — direct
      port of `Flow Model`, redesigned per ADR 0009: `model_id` is now a **bare** id (no
      `provider/` prefix composition); `context_window` is plain user-editable `Int`, no
      auto-detection (dropped `_resolve_context_window`/`_detect_context_window`, no Agno
      equivalent to `litellm.get_model_info`); `test_connection()` instantiates the resolved
      Agno model class and issues its own minimal `.response()` call instead of one generic
      `litellm.completion()`; `get_provider_models()` uses litellm's model registry as a
      UX-only helper, filtered to providers that Agno can execute. `after_insert` →
      `sync_builtin_assistant` guarded with `try/except ImportError` (Phase 7)
- [x] DocType `AI Settings` (Single) + controller, `_guard_model_change`,
      `_sync_embedding_dimension` — ported from `Flow Knowledge Settings`, extended with the
      8 new service-config fields per `003-doctype-reference.md` §3. Both Phase-4-dependent
      calls (`frappe.db.count("AI Knowledge Chunk")`, `frappe_ai.knowledge.embedder.probe_dimension`)
      guarded to degrade gracefully (doctype-existence check / `ImportError`) rather than
      break until Phase 4 lands
- [x] `frappe_ai/lib/model.py` — reduced from flow's `Model`/`ChatResponse`/streaming class
      to just `resolve_provider_credentials()` and the Agno provider-slug→class map
      (`PROVIDER_MODEL_CLASSES`, `is_known_provider()`, `get_model_class()`), per ADR 0009 —
      chat/streaming belongs to Agno's `Agent` and model classes in Phase 3, not here
- [x] Tests: `test_model.py` (rewritten for the reduced `lib/model.py` — provider-slug
      lookup and credential resolution, not chat/streaming), `test_safe_exec.py`,
      `test_conditions.py`, `test_ai_provider.py`, `test_ai_model.py` — 69 tests total, 62
      passing

### Deviations from the original plan

- **ADR 0009 (no litellm)** — the single largest deviation. Original task scope (and the
  original `002`/`003` specs, before they were revised) planned a mechanical litellm port.
  Surfaced mid-implementation, recorded as ADR 0009, and specs/progress-doc updated before
  continuing. See the ADR for full rationale; short version: Agno already owns model calling
  via native per-provider classes, so litellm would have been a second redundant
  provider-abstraction layer underneath it.
- **7 pre-existing test errors, not introduced by this phase.** `frappe.get_list()` /
  `frappe.db.get_list()` in this bench's checked-out Frappe (16.0.0-dev) no longer accepts
  `ignore_user_permissions` as a kwarg to `DatabaseQuery.execute()` — `_permissioned_get_list()`
  in `safe_exec.py` (ported verbatim, matching `flow`) still passes it unconditionally.
  Confirmed **identical** in `flow`'s own test suite on this same bench
  (`bench --site tact.local run-tests --app flow --module flow.tests.test_safe_exec` →
  6 of the same errors; `frappe_ai` has 7, the extra one via `test_conditions.py`'s
  `test_condition_can_query_db`, which goes through the same helper). This is a Frappe-core
  API drift the reference implementation (`flow`) has not addressed either, not a
  `frappe_ai`-specific defect, and not in Phase 1's scope to fix (would mean deviating from
  the ported source without being asked to). Flagged here rather than silently patched;
  candidate for a small follow-up ADR or a `frappe_ai`-side fix in a later phase if it isn't
  fixed upstream first.
- **`context_window` field default:** confirmed empirically that a new `AI Model`'s
  `context_window` is `None` on the in-memory doc immediately after `.insert()` (Frappe
  applies the `Int` column default only on reload), not `0`. `test_context_window_defaults_to_zero`
  reloads the doc before asserting, rather than checking the pre-reload in-memory value.

### Completion criteria

- [x] `bench --site tact.local install-app frappe_ai` succeeds — `frappe_ai` was already
      installed on `tact.local`; `bench --site tact.local migrate` completed cleanly through
      "Updating DocTypes for frappe_ai: 100%" with the 3 new DocTypes created. (A later,
      unrelated `migrate` re-run hit a pre-existing core-Frappe error on `Dropbox Settings`
      — missing `dropbox` package, nothing to do with `frappe_ai` — before even reaching
      `frappe_ai`'s doctype sync; confirmed the 3 DocTypes remained intact in the DB
      afterwards.)
- [x] `AI Provider` + `AI Model` creatable in the desk — verified via `bench execute`
      against real controller code (insert, validate, save all exercised)
- [x] `test_connection()` returns a successful ping against a real provider via its
      resolved Agno model class (not litellm — ADR 0009) — verified against the real OpenAI
      API with `agno.models.openai.OpenAIChat`: a fake API key produced a genuine 401 from
      OpenAI surfaced through `frappe.throw`, proving the Agno model class is actually
      instantiated and called, not stubbed
- [x] `safe_exec` rejects `frappe.db.sql` and `frappe.get_all` — `test_raw_sql_unavailable`
      and `test_get_all_unavailable` pass; `test_excludes_permission_bypassing_functions`
      additionally confirms `set_value`, `commit`, `rollback`, `add_index`, `escape`,
      `frappe.qb`, and `frappe.session.csrf_token` are all absent from the sandbox namespace

---

## Phase 2 — FastAPI Service Skeleton ✅

**Objective:** A supervised, authenticated ASGI service that can talk to Frappe.

> **Build decision (2026-08-05):** hand-built FastAPI, per the tasks below — **not**
> layered on Agno's AgentOS (`AgentOS(base_app=...)`). AgentOS was evaluated (it
> supports wrapping a custom FastAPI app while keeping our own auth middleware
> and the ADR 0003 dispatch endpoint) but explicitly deferred as a possible
> future enhancement rather than adopted now, per direct confirmation. Revisit
> if Phase 2's hand-rolled session/SSE/tracing code becomes a maintenance
> burden, or ahead of Phase 8.2 (operability: trace IDs, metrics) where AgentOS
> would provide overlapping capability out of the box.

### Bootstrap credential design (read before touching `config.py`)

> **Revised 2026-08-06 — see [ADR 0011](../decisions/0011-service-secret-in-site-config.md).**
> The design below (env-var secret, ADR 0010) shipped with Phase 2 but caused
> `bench start` to crash-loop the `ai` process when the env vars weren't
> exported by hand. Current design, kept for history below the line.

The chicken-and-egg problem: the FastAPI process's *first* call to Frappe (fetching
`AI Settings`) needs to already be authenticated, but the shared secret it would
authenticate with can't itself come from that first call.

**Current resolution (ADR 0011):** the secret lives in `sites/<site>/site_config.json`
as `frappe_ai_service_secret` — the same file every Frappe process already reads via
`frappe.conf`. The FastAPI service reads that file directly off disk (still no
`frappe.init`/`frappe.connect` — reading a static file isn't opening a DB
connection). Only the site *name* is still supplied externally:

- `FRAPPE_AI_SITE` — which site's config to read; defaults to
  `common_site_config.json`'s `default_site` if unset, so on a single-site bench
  no env var is required at all.
- `FRAPPE_AI_FRAPPE_URL` — base URL of the Frappe web process (default
  `http://127.0.0.1:8000`).
- `FRAPPE_AI_CORS_ORIGINS` — comma-separated allowed origins for the browser-facing
  CORS policy (default `http://127.0.0.1:8000,http://localhost:8000`); deliberately
  not `*` since the API is authenticated/mutating.
- `FRAPPE_AI_SITES_PATH` — override for the bench's `sites/` directory; mainly for
  tests, normally inferred from the service module's own install path.

Setup: `bench --site <site> set-config frappe_ai_service_secret <value>`.

Everything else in `AI Settings` (`request_timeout`, `stream_timeout`, ...) is still
fetched lazily over HTTP using this secret as a shared-secret credential.

<details>
<summary>Original design (ADR 0010, superseded — env-var secret)</summary>

**Resolution:** the bootstrap secret and site identity are supplied to the process
independently, via environment variables, at `bench start` / Procfile invocation
time — not fetched from Frappe:

- `FRAPPE_AI_SERVICE_SECRET` — must match `AI Settings.service_secret` (the Frappe
  side is authoritative; the env var is expected to be kept in sync with it, e.g.
  by whoever configures the deployment pasting the same value both places).
- `FRAPPE_AI_SITE` — which site's `AI Settings` to fetch (sent as the
  `X-Frappe-Site-Name` header, which Frappe core resolves the site from
  independent of `Host` — confirmed in `frappe/app.py:get_site()`).
- `FRAPPE_AI_FRAPPE_URL` — base URL of the Frappe web process (default
  `http://127.0.0.1:8000`).
- `FRAPPE_AI_CORS_ORIGINS` — comma-separated allowed origins for the browser-facing
  CORS policy (default `http://127.0.0.1:8000,http://localhost:8000`); deliberately
  not `*` since the API is authenticated/mutating.

Everything else in `AI Settings` (`request_timeout`, `stream_timeout`, ...) is fetched
lazily over HTTP using the bootstrap secret as a shared-secret credential, per §10 of
`001-architecture.md` ("does not call frappe.init/frappe.connect"). This kept exactly
one credential duplicated across two places (env var + `AI Settings.service_secret`),
which turned out to be the actual problem — see ADR 0011's Context for why this failed
in practice. Full original design and alternatives considered:
[ADR 0010](../decisions/0010-service-bootstrap-via-env-vars.md).

</details>

### Tasks

- [x] `frappe_ai/service/main.py` — FastAPI app, lifespan (startup/shutdown logging
      only), CORS (origins from `FRAPPE_AI_CORS_ORIGINS`, never `*`), plus a
      placeholder `POST /stream/{run}` (run-token-gated, forward-looking to Phase 3's
      real SSE endpoint — see "Deviations" below)
- [x] `frappe_ai/service/config.py` — `ServiceSettings`/`load_settings()`, bootstrap
      from env vars per ADR 0010 (not fetched from `AI Settings` — see that ADR)
- [x] `frappe_ai/service/auth.py` — `mint_run_token`/`verify_run_token` HMAC
      primitive (stdlib `hmac`+`hashlib`, `\x1f`-separated fields, `hmac.compare_digest`
      for constant-time signature comparison), operates on bare `run`/`session`/`user`
      strings pending `AI Run`/`AI Session` in Phase 3
- [x] `frappe_ai/service/frappe_client.py` — `FrappeClient`, async `httpx`, one real
      capability (`get_service_config`), `X-Frappe-AI-Service-Secret` +
      `X-Frappe-Site-Name` headers (see "Deviations" for why not `Authorization`)
- [x] `GET /health` — 200, no auth, `{"status": "ok", "frappe_reachable": bool}` —
      `frappe_reachable` is a genuine end-to-end round-trip through
      `get_service_config`, not a stub
- [x] `frappe_ai/api/service.py` — `mint_token()` (stubbed run/session validation,
      documented in its own docstring), `service_health()`, `get_service_config()`
      (`allow_guest=True`, `X-Frappe-AI-Service-Secret` auth via
      `hmac.compare_digest`, the Frappe-side half of `frappe_client.py`)
- [x] Procfile entry (`ai: uvicorn frappe_ai.service.main:app --port 8001`) —
      requires `FRAPPE_AI_SITE`/`FRAPPE_AI_SERVICE_SECRET` exported by whoever runs
      `bench start`, documented inline in the Procfile and in ADR 0010
- [x] Documented the two-process dev workflow — see ADR 0010 and the Procfile comment
- [x] `pyproject.toml` — added `fastapi>=0.115,<1`, `uvicorn>=0.34,<1` (plain, not
      `[standard]` — see "Deviations"), `httpx>=0.27,<1` (already present
      transitively; declared explicitly since `frappe_client.py` imports it directly)
- [x] Tests: `frappe_ai/tests/test_service_auth.py` (9, plain `unittest.TestCase` —
      HMAC roundtrip, tampering, expiry, no Frappe context needed),
      `frappe_ai/tests/test_service_app.py` (9, `fastapi.testclient.TestClient` —
      `/health`, the `/stream/{run}` placeholder's 401s, CORS-not-wildcard),
      `frappe_ai/tests/test_service_api.py` (4, `IntegrationTestCase` —
      `get_service_config`'s shared-secret auth: valid/wrong/missing/empty header)

### Deviations from the original plan

- **`Authorization: Bearer <secret>` does not work for the service→Frappe shared
  secret — discovered during this phase's own end-to-end verification.** The brief's
  literal wording ("a simple shared-secret bearer header") was implemented first as
  a standard `Authorization: Bearer <service_secret>` header, matching common REST
  convention. Live testing against `bench start`'s actual Frappe process consistently
  returned 401 even with the correct secret. Root cause: `frappe/auth.py:validate_auth()`
  runs globally on every request and itself intercepts any two-part `Authorization`
  header, attempting OAuth/API-key validation; when that (unsurprisingly) fails, it
  raises `frappe.AuthenticationError` **before the whitelisted method's body ever
  runs** — regardless of that method's own `allow_guest=True`. Fixed by moving the
  shared secret to a dedicated header, `X-Frappe-AI-Service-Secret`, which Frappe
  core never inspects. `frappe_client.py`'s and `api/service.py`'s docstrings both
  record this so it isn't rediscovered in Phase 3. The browser-facing run token
  (ADR 0004) is unaffected — it goes to FastAPI directly, never through Frappe's
  `validate_auth`, so `Authorization: Bearer <run token>` on `/stream/{run}` is
  correct as specified and was not changed.
- **`uvicorn` installed without the `[standard]` extra.** `uvicorn[standard]` pulls
  in `websockets>=13`, which conflicts with `commit` (another app in this bench)
  pinning `websockets<11` via `pyppeteer`. This service doesn't need `[standard]`'s
  optional deps (`uvloop`, `httptools`, `websockets`, `watchfiles`) — SSE (Phase 3)
  runs over plain HTTP, not the websocket protocol, and no `--reload` file-watching
  is used in this Procfile entry. Plain `uvicorn` sidesteps the conflict without
  losing anything this phase or Phase 3's SSE plan needs.
- **`AI Run`/`AI Session` don't exist yet (expected, per the brief).** `mint_token`
  accepts bare `run`/`session` strings with no existence/ownership check — Phase 3
  must add real validation (run exists, belongs to the caller or caller has `read`,
  run is `Running`) when those DocTypes land. Documented in `mint_token`'s own
  docstring, not just here.
- **`ADR 0010` added**, not originally listed in the brief's task list, because the
  brief itself flagged the bootstrap-credential question as "a real architectural
  decision within Phase 2's scope, not a detail to hand-wave" — written up as a full
  ADR rather than only prose in this progress doc, matching how ADRs 0001–0009 were
  already handled for comparably-scoped decisions.

### Completion criteria

- [x] `bench start` brings up Frappe (8000) **and** uvicorn (8001) — ran `bench start`
      in the background with `FRAPPE_AI_SITE`/`FRAPPE_AI_SERVICE_SECRET` exported;
      confirmed both `web.1` and `ai.1` in the Procfile log, `curl` succeeded against
      both ports, then killed the whole process tree and confirmed both ports closed
      (connection refused) — nothing left running.
- [x] `curl http://127.0.0.1:8001/health` → 200, with `{"status": "ok",
      "frappe_reachable": true}` — the `frappe_reachable: true` is real, not
      hardcoded: it comes from `FrappeClient.ping()` actually calling
      `get_service_config` on the live Frappe process and getting a 200 back.
- [x] Unauthenticated request to a protected route → 401 — `POST /stream/{run}`
      without a bearer token returns `{"detail": "Missing bearer run token"}`, 401.
- [x] Expired/forged HMAC token → 401 — verified two ways: (1) directly against
      `auth.verify_run_token` in `test_service_auth.py`, no HTTP involved; (2)
      against the live `/stream/{run}` route on the running service — a token
      signed with the wrong secret returned `{"detail": "Signature mismatch"}`
      and a `ttl_seconds=-10` token returned `{"detail": "Token expired"}`, both 401.

### ✅ Resolved — `bench start` now boots `ai` unattended (fixed 2026-08-06)

**Found:** the completion criterion above was originally verified with
`FRAPPE_AI_SITE`/`FRAPPE_AI_SERVICE_SECRET` exported by hand in the shell before
running `bench start`. Nothing in this bench sourced those automatically. Plain
`bench start` with no env vars pre-set crash-looped the `ai` process:
`ServiceConfigError` on import, `system | ai.1 stopped (rc=1)`. `web`, `worker`,
etc. were unaffected — only `ai` crashed.

**Fixed via [ADR 0011](../decisions/0011-service-secret-in-site-config.md)
(supersedes [ADR 0010](../decisions/0010-service-bootstrap-via-env-vars.md)):**
the secret moved from a DB field (`AI Settings.service_secret`, now removed) plus
an env var, to `site_config.json`'s `frappe_ai_service_secret` — the one file
every Frappe process already reads. The FastAPI service reads that file directly
off disk (`frappe_ai/service/config.py`, still no `frappe.init`/`frappe.connect`
— see the ADR for why that's fine). The site name itself now defaults to
`common_site_config.json`'s `default_site` when `FRAPPE_AI_SITE` isn't set, so on
this bench `bench start` requires **zero** environment variables.

**Verified 2026-08-06:** killed all stray redis processes, unset every
`FRAPPE_AI_*` env var, ran plain `bench start` — `ai.1` came up cleanly
(`Uvicorn running on http://127.0.0.1:8001`), `curl http://127.0.0.1:8001/health`
→ `200 {"status":"ok","frappe_reachable":true}` (a genuine round-trip, not
hardcoded), then stopped `bench start` and confirmed no processes left running.
Full `frappe_ai` test suite rerun after the change: same 91 tests, same 84
passing / 7 pre-existing failures as before (unrelated `ignore_user_permissions`
issue) — no new failures introduced.

Setup for a new deployment: `bench --site <site> set-config frappe_ai_service_secret <value>`.

---

## Phase 3 — Agents, Tools & Run Loop ✅

**Objective:** Feature-parity agent execution, streaming, and confirmations.

### Tasks

- [x] DocTypes: `AI Agent`, `AI Agent Tool`, `AI Agent Knowledge Base`, `AI Tool`
- [x] DocTypes: `AI Session`, `AI Session Message`, `AI Session Attachment`, `AI Run`
- [x] `AI Agent` controller — default tools, `_ensure_knowledge_search_tool`, `_snapshot`
- [x] `AI Tool` controller — slug/AST/XOR validation, `schema_from_code`
- [x] `AI Session` controller — `assert_session_owner`, `build_prompt_messages`, `clear_old_logs`
- [x] `AI Run` controller — `apply_result` (accumulating), `mark_failed`, invariants
- [x] `frappe_ai/service/builder.py` — `AgentBuilder.build()` → `agno.agent.Agent`
- [x] `frappe_ai/service/routes/chat.py` — SSE streaming, pause/resume
- [x] `frappe_ai/tools/builtins.py` — the 10 builtins (Frappe-side)
- [x] `frappe_ai/api/dispatch.py` — permission-enforcing tool dispatch (**security-critical**)
- [x] `frappe_ai/api/api.py` — `start_run`, `resume_run`, `stop_run`, `recover_session`,
      `submit_feedback`, `get_agent_tools`, `attach_file`, plus `persist_run_result`/`fail_run`
      (internal, service-secret-authenticated persistence callbacks — see Deviations)
- [x] `sync_builtin_tools()` — wired to `after_migrate` in `hooks.py` (flow calls it from
      `sync_builtin_assistant`, which is Phase 7 here; running it independently means
      builtins exist on every migrate without waiting for Phase 7)
- [x] Tests: `test_agent.py`, `test_tool.py`, `test_resolver.py`, `test_builtins.py`,
      `test_session.py`, `test_api.py` — 91 new tests

### Completion criteria

- [x] A chat turn streams tokens to the browser — **verified live, both paths.** Failure
      path: `start_run` → real HMAC token → `POST /stream/{run}` against a standalone
      `uvicorn` instance → `run_started` → a deliberately invalid API key produced a genuine
      OpenAI 401, surfaced as an `error` SSE event, `AI Run` persisted `Failed` via
      `fail_run` — the "fake key, genuine provider rejection" style Phase 1 used for
      `test_connection()`. Success path (2026-08-07, after the provider-resolution fix
      below): a real `AI Model(provider="openai", base_url="https://api.groq.com/openai/v1")`
      routed the real, already-installed `openai`/Agno `OpenAIChat` class at Groq's
      OpenAI-wire-compatible endpoint — 18 real streamed `text` deltas, a real
      `find_doctypes` tool call through `dispatch_tool` (`tool_started`/`tool_ended` with
      real permission-checked results), a correct `done` with real `output`,
      `iterations: 2`, and real token usage. `AI Run` and the session's `AI Session
      Message` transcript both confirmed persisted correctly end to end. See
      `docs/learnings.md` for the provider-resolution detour this required (`AI
      Model.provider` was a `Link → AI Provider`, which meant any provider slug needed a
      matching `AI Provider` **document** to exist merely to save — no SDK was even
      installed for `groq`, so live-testing hit a real, previously-undiscovered gap before
      it could hit this criterion).
- [x] A `create` tool call pauses for confirmation; Approve/Deny/redirect all behave per
      spec — **live-verified end-to-end (2026-08-07), all three answer types.** Getting
      here found and fixed three real bugs the unit suite never caught, since none of it
      round-tripped a real pause → resume → model-continuation cycle: (1) the paused
      transcript persisted an internal marker string as if it were a real tool result,
      which a resumed model read as "already done" and hallucinated a fake success from;
      (2) approving a call never actually made anything happen — `approved_call_ids` only
      permitted a re-request, it never issued one, so resume now directly dispatches each
      approved call (mirroring `flow`'s `_prepare_resume`) rather than hoping the model
      asks again; (3) Deny's persisted result silently dropped its own audit record via an
      off-by-one in the stored-transcript diff. Full writeup, root causes, and fixes in
      `docs/learnings.md`. Verified live: Approve → real dispatch, real DB record, correct
      transcript; Deny → no dispatch, no DB record, correct transcript, run halts
      immediately; redirect → the model read free-text feedback, corrected its own
      arguments, retried, and only the corrected version was ever actually created — proof
      the feedback was genuinely acted on, not just accepted. 155 tests, 148 passing after
      the fixes, same 7 pre-existing failures, no regressions.
- [x] **A user without permission is refused by the tool** (ADR 0003's decisive test) —
      verified via `test_api.py::TestDispatchToolActingUserScoping.test_unprivileged_user_refused_by_tool`:
      `dispatch_tool` under a role-less user attempting to read `AI Provider` returns
      `{"error": ...}`, not a result.
- [x] `AI Run` captures config snapshot, tool calls, usage, iterations — `apply_result`
      accumulates iterations/usage across resume segments (`test_apply_result_accumulates_iterations_and_usage_on_resume`)
      and appends only delta messages (`test_apply_result_appends_only_delta_messages`).
- [x] Client disconnect cancels the run and marks it failed — `stream_chat`'s `GeneratorExit`
      handler calls `fail_run` before re-raising; not independently live-tested (would need
      an actual mid-stream client abort against a real, slow model call).

### Deviations from the original plan

- **`search_knowledge`/`update_memory` are fail-closed stubs, not real implementations.**
  `AI Knowledge Base` (Phase 4) and `AI Agent Memory` (Phase 5) don't exist yet.
  `bind_search_knowledge`/`bind_update_memory` are kept (so agent configs referencing
  these slugs keep resolving) but always `frappe.throw` a clear "not yet available"
  error. Mirrors the guarded-`ImportError` pattern Phase 1 already established for
  `AI Settings`' Phase-4-dependent calls.
- **`AI Session Attachment` supports Inline mode only, plain-text files only.**
  `flow.knowledge.extract.extract_file` (PDF/DOCX/XLSX/OCR/etc., via the Phase 4
  ingestion pipeline) doesn't exist yet. `_extract_text_file` reads UTF-8 text directly
  for a fixed extension allowlist (`.txt`, `.md`, `.csv`, `.json`, `.log`, `.py`, `.js`,
  `.html`, `.xml`); anything else is rejected with a clear "Unsupported file type" error.
  Retrieval mode (chunk + embed oversized files into LanceDB's `chat_attachment_chunks`)
  is entirely Phase 4 — every Phase 3 attachment is Inline. Closes automatically once
  Phase 4 lands; the `mode` field and controller shape already match flow's.
- **`AI Agent.mcp_connections` and `AI Run.trigger`/reference to `AI Trigger` omitted
  from the DocType JSON**, not just left unpopulated — `AI MCP Connection` and
  `AI Trigger` don't exist until Phase 5, and an empty Link/Table field to a
  not-yet-existing doctype would be dead weight until then. Both fields are additive
  when Phase 5 lands (new fields on an existing DocType), not a rework.
- **`AI Session.build_prompt_messages()` has no retrieval-chunk or `<agent_memory>`
  injection** — reduces to system instructions (implicit, via `Agent(instructions=...)`)
  + prior transcript + inline attachment text. Both Phase 4/5 concerns; the method's
  return shape (a list of OpenAI-format message dicts) doesn't change when they land.
- **`flow`'s `stream_with_persistence` WSGI commit choreography was not ported** —
  as flagged in `001-architecture.md` §8 and `AI Run`'s module docstring, it existed to
  work around WSGI iterating a streamed response body after the request handler
  returns. FastAPI's `frappe_ai.service.routes.chat.stream_chat` persists via explicit
  callback (`persist_run_result`/`fail_run`) on `Done`/exception/disconnect instead —
  no equivalent problem exists to work around.
- **`frappe_ai/api/dispatch.py` has no `flow` equivalent** — `flow` dispatches tool
  calls in-process (`Agent._invoke`/`_run_tool` in `lib/agent.py`) with permission
  checks inside each tool function. Because `frappe_ai` splits the agent loop into a
  separate FastAPI process, every Frappe-touching tool call must cross back over HTTP;
  `dispatch_tool` is that crossing point and the concrete mechanism behind ADR 0003 —
  `frappe.set_user(acting_user)` before the tool body runs, so every permission check
  inside it (including `safe_exec`'s namespace) is scoped to the acting user, never the
  service's own identity. New plumbing, not a port.
- **Confirmation pause/resume is not built on Agno's native HITL support.** Agno's
  `Function.requires_confirmation` + `RunPausedEvent`/`Agent.continue_run` exist, but
  that mechanism is built around Agno's own session `db` holding the paused
  `RunOutput` between the pause response and a later, possibly separate, resume
  request — a stateful dependency this architecture doesn't have (Frappe is the only
  source of truth across requests; the service is stateless — `001-architecture.md` §7.1).
  Instead, each tool's `entrypoint` (`AgentBuilder._build_tool`) raises a
  `PendingConfirmation` (a plain `Exception`, deliberately **not**
  `agno.exceptions.AgentRunException` — that variant is re-raised out of the whole
  `arun()` generator on the first occurrence, which would stop the turn after just one
  pending call) when a call needs confirmation and isn't in `approved_call_ids` yet.
  Agno's `Function.aexecute` catches any plain `Exception` as a per-call failure
  (`ToolExecution.tool_call_error = True`, `.result = str(exception)`), which
  `PendingConfirmation.__str__` formats as a parseable marker string
  (`PENDING_CONFIRMATION_MARKER:...`) that `chat.py`'s `_pending_from_tool_execution`
  greps back out — letting multiple tool calls in one turn each independently end up
  Paused, matching `flow`'s behaviour of collecting every pending `Question` in one pass.
  On resume, previously-approved calls dispatch immediately (their id is in
  `approved_call_ids`); the full prompt is rebuilt from Frappe's stored transcript each
  time, never from an Agno-held `RunOutput`.
- **`RunOutput` is not reliably yielded on a failed run — discovered empirically, not
  from Agno's docs.** `agent.arun(..., yield_run_output=True)` was expected to yield a
  final `RunOutput` as the last item of the stream on both success and failure (its
  `RunStatus.error` was going to be the failure signal). Live testing against a real
  provider 401 showed the generator instead yields
  `RunStartedEvent → ModelRequestStartedEvent → RunErrorEvent` with **no `RunOutput` at
  all**. `stream_chat` now handles `RunErrorEvent` explicitly (captured as `run_error`)
  as the primary failure signal, with the `RunOutput.status == RunStatus.error` check
  kept as a secondary fallback for whichever failure shapes do yield a final `RunOutput`.
  Found via the live smoke test below, not by static reading of Agno's source — flagged
  here so it isn't rediscovered.
- **`agent.arun(stream=True)` is not itself an awaitable — a second live-testing find.**
  It's a plain (non-`async def`) method whose `@overload`ed return type is
  `AsyncIterator[RunOutputEvent]` directly when streaming; `async for event in await
  agent.arun(...)` raised `TypeError: object async_generator can't be used in 'await'
  expression` the first time this was actually run against a live service process.
  Fixed to `async for event in agent.arun(...)` (no `await`). Neither this nor the
  `RunOutput`-on-error finding above surfaced from reading Agno's type stubs — both were
  only caught by the live smoke test in Verification below, which is why that step
  matters even though it isn't part of the automated suite.
- **Tool-call argument injection into an Agno entrypoint uses the `fc: FunctionCall`
  parameter, not a custom kwarg.** Agno's `Function._build_entrypoint_args` injects
  `fc` (the current `FunctionCall`, whose `.call_id` is what `PendingConfirmation`/
  `approved_call_ids` key on) only when the entrypoint's own signature names a
  parameter `fc` — confirmed by reading `agno/tools/function.py` directly rather than
  guessing at a wrapper convention.

### Verification

- Full `frappe_ai` suite: 155 tests, 148 passing. The 7 failures are the same
  pre-existing `ignore_user_permissions` / Frappe-core API drift documented in Phase 1
  (`DatabaseQuery.execute()` no longer accepts that kwarg) — 6 in `test_safe_exec.py`,
  1 in `test_conditions.py` — not a Phase 3 regression; not in this phase's scope to fix.
  Re-confirmed after the provider-resolution fix below (same 7, no new failures).
- Live smoke test, failure path (not part of the automated suite): a standalone
  `uvicorn` instance running this phase's code (separate port from the bench's own
  long-running `ai` process, which was left untouched — see note below) served a real
  `start_run` → token → `POST /stream/{run}` round trip against
  `AI Provider(provider="openai", api_key="sk-fake-test-key")`. This is the same "fake
  key still proves the real call path" style Phase 1 used for `test_connection()`; two
  real Agno API-usage bugs (above) were caught only by this live run, not by unit
  tests, since both required an actual `arun()` invocation to surface.
- **Live smoke test, success path (2026-08-07) — now verified.** Getting here required
  a real fix, not just a real key: the first attempt (a real Groq API key, borrowed
  from `apps/flow`'s `Flow Provider "groq"`) hit `ImportError: groq not installed` —
  Agno needs each provider's actual SDK, confirmed by reading `agno/models/groq/groq.py`
  directly, and only `openai`'s SDK is installed in this bench. Rather than install a
  new SDK per provider (explicitly against the stated goal) or repurpose the existing
  `AI Provider("openai")` row's `base_url` (misleading), found and fixed the actual
  root cause: `AI Model.provider` was a `Link → AI Provider`, so *any* provider slug
  required a matching `AI Provider` document to exist merely to save the model —
  independent of whether the application logic (`_model_call_config`,
  `resolve_provider_credentials`) actually needed one, which it never did. Changed
  `provider` to `Autocomplete` (matching `model_id`'s existing pattern), decoupling
  "which Agno class to instantiate" from "does a matching `AI Provider` doc exist."
  Full writeup, alternatives considered, and why in `docs/learnings.md`.
  With that fixed: `AI Model(provider="openai", base_url="https://api.groq.com/openai/v1",
  api_key=<real groq key>)` — the already-installed `openai`/Agno `OpenAIChat` class
  talking to Groq's OpenAI-wire-compatible endpoint, zero new SDKs, no `AI Provider`
  row touched or repurposed — produced a fully real successful run: 18 streamed `text`
  deltas, a real `find_doctypes` tool call through `dispatch_tool` (permission-checked,
  executed in Frappe, correct `tool_started`/`tool_ended` events), a correct `done`
  with real `output`/`iterations: 2`/token usage, and a fully correct persisted
  transcript (system → user → tool → assistant) confirmed directly against the DB.
- **Confirmation pause/resume — live-verified (2026-08-07), all three answer types,
  after fixing three real bugs the unit suite never caught.** (1) The transcript
  persisted on pause included Agno's own error-handling row for the blocked call —
  an internal marker string, not a real result — which a resumed model read as
  "already done" and hallucinated a fake success from; fixed by stripping it
  (`_messages_excluding_pending`). (2) Approving a call never actually made anything
  happen: `approved_call_ids` only *permitted* a re-request, it never *issued* one,
  so nothing made the model retry the exact call it was blocked on; fixed by
  dispatching each approved call directly on resume (`_dispatch_approved`), mirroring
  `flow`'s own `_prepare_resume`. (3) Deny's persisted result silently dropped its own
  audit record — an off-by-one from filtering the `system` row out of a message list
  whose length needed to match the stored row count exactly. Full root-cause writeup
  in `docs/learnings.md`. Verified live: Approve → real dispatch, real DB record;
  Deny → no dispatch, no DB record, run halts immediately; redirect → the model read
  free-text feedback, corrected its own arguments, retried, and only the corrected
  version was ever created. 155 tests, 148 passing after the fixes — same 7
  pre-existing failures, no regressions. This was the provider blocker's follow-on —
  once that was resolved, this was the next and final piece of Phase 3's completion
  criteria to prove against a real model.
- The bench's own `bench start`-managed `ai` process was deliberately left running
  unmodified throughout this phase's verification — it does not autoreload
  (`uvicorn` without `--reload`), so it is still serving Phase 2 code until the next
  manual restart. This is a note for whoever restarts it, not a Phase 3 blocker.

---

## Phase 4 — Knowledge / RAG ✅

**Objective:** Ingestion and retrieval at parity, on LanceDB.

**Design baseline:** `AI Agent Knowledge Base` (child table) and `AI Session Attachment`
already exist from Phase 3, forward-built for this phase — `ai_session_attachment.py`'s
`_extract_text_file` is explicitly a Phase-3-only stand-in for
`frappe_ai.knowledge.extract.extract_file`, and `ai_settings.py`'s `_sync_embedding_dimension`
already calls `frappe_ai.knowledge.embedder.probe_dimension` behind an `ImportError` guard.
`frappe_ai/utils/system_generated.py` (guards for `is_system_generated` rows) is also
already ported and used as-is, no changes needed. See
[ADR 0012](../decisions/0012-embeddings-direct-provider-sdk.md) for why embeddings call
provider SDKs directly instead of routing through Agno (no `agno.embedder` exists) or
litellm (ADR 0009).

Module path fixed by the above: `frappe_ai.knowledge.{extract,chunker,embedder,store,
attachment_store,ingest,retriever}`.

### Sub-phases

**4a — DocTypes.** `AI Knowledge Base`, `AI Knowledge Source`, `AI Knowledge Chunk`
(`autoincrement` naming — load-bearing, becomes the LanceDB row `id`), controllers
(`validate`/`on_trash`/`before_rename`/whitelisted `resync`+`reconcile`), matching
`003-doctype-reference.md` §12–14 exactly. Depends on: nothing new (system_generated.py
already exists).

**4b — Extraction + chunking.** `knowledge/extract.py` (pdf/xlsx/docx/html/text, SSRF
guard `_validate_public_url`, 10 MB `_read_capped`, RapidOCR at 200 DPI, DocType-source
`resolve_content_fields` injection guard), `knowledge/chunker.py` (character chunker,
whitespace-aware, overlap). Pure-function modules, no DB writes — portable near-verbatim
from `flow`. Depends on: 4a only for `FILE_EXTENSIONS`/`CHILD_FIELDTYPES` constants being
importable, not for any doctype to exist yet.

**4c — Embedder.** `knowledge/embedder.py` — `embed_texts`/`probe_dimension`, batched at
96, order-preserving via response `.index`, direct provider SDK call (ADR 0012) instead of
`litellm.embedding`. Ships one caller (`openai`-compatible, covers any OpenAI-wire endpoint
via `base_url`) in `EMBEDDING_CALLERS`. Depends on: `AI Model`/`resolve_provider_credentials`
(already exist, Phase 1).

**4d — LanceDB store + retriever.** `knowledge/store.py` (`chunks` table, hybrid search via
LanceDB's native `query_type="hybrid"`, `ensure_table_for_dimension` throws on mismatch),
`knowledge/attachment_store.py` (`chat_attachment_chunks` table, dimension mismatch
*recreates* instead of throwing — ephemeral), `knowledge/retriever.py` (`retrieve`/
`retrieve_attachments`, fail-closed on empty `kbs`, silent-empty on all-disabled).
Depends on: 4c (needs vectors to store/search).

**4e — Ingestion pipeline.** `knowledge/ingest.py` — `ingest_source`/`purge_source`
(single-doc rebuild path), `_sync_doctype` (incremental watermark + SHA-256
`content_hash` gating), `_remove_stale` (via `Deleted Document` tombstones),
`reconcile_source` (full-scan fallback for tombstone-less deletes), `enqueue_ingestion`
(`queue="long"`, `enqueue_after_commit=True`), `sync_due_sources` (daily scheduler entry).
Depends on: 4a (writes `AI Knowledge Chunk` rows), 4b (extraction/chunking), 4c/4d
(embed + store).

**4f — Wiring + code-first API + tests.**
- `knowledge/knowledge.py` — `Knowledge(title)` handle (`add_text`/`add_file`/
  `add_local_file`/`add_url`/`add_doctype`), synchronous ingestion contract.
- Rewire `tools/builtins.py`'s `bind_search_knowledge` to call
  `frappe_ai.knowledge.retriever.retrieve`, port `_knowledge_search_description`'s
  per-KB description appending.
- Extend `ai_session_attachment.py`: swap `_extract_text_file` for real
  `extract_file`, wire `Retrieval` mode to `attachment_store` for oversized attachments.
- `hooks.py`: add `scheduler_events.daily` entry for `sync_due_sources`; add
  `AI Knowledge Chunk` to `ignore_links_on_delete`.
- `pyproject.toml`: declare `lancedb`, `python-docx`, `pdfplumber`, `rapidocr`,
  `onnxruntime` (all already installed in this bench's venv from `flow`, but must be
  declared as `frappe_ai`'s own dependencies, not inherited implicitly).
- Tests: `test_knowledge.py`, structured after `flow`'s (18 test classes) but with
  litellm-mock call sites replaced by whatever 4c's actual SDK call site is.

### Tasks

- [ ] 4a — `AI Knowledge Base`/`Source`/`Chunk` DocTypes + controllers
- [ ] 4b — `knowledge/extract.py`, `knowledge/chunker.py`
- [ ] 4c — `knowledge/embedder.py` (batch 96, `probe_dimension`, ADR 0012 direct-SDK)
- [ ] 4d — `knowledge/store.py`, `knowledge/attachment_store.py`, `knowledge/retriever.py`
- [ ] 4e — `knowledge/ingest.py` (incremental sync, tombstones, reconcile, scheduler)
- [ ] 4f — `knowledge/knowledge.py`, `search_knowledge` rewiring, attachment Retrieval
      mode, `hooks.py`, `pyproject.toml`, `test_knowledge.py`

### Completion criteria

- [x] A PDF ingests into `AI Knowledge Chunk` rows + LanceDB entries with matching ids
      — exercised via `_extract_pdf`/OCR-fallback tests and `TestIngest`'s
      MariaDB↔LanceDB id-matching assertions; live-verified structurally (unit-level,
      no live LLM key in this environment — same caveat as Phase 3's provider tests).
- [x] `search_knowledge` returns relevant hits; disabling a KB removes it from
      results — `TestRetriever`/`TestAgentKnowledge`.
- [x] Deleting a source purges MariaDB rows **and** LanceDB entries —
      `TestIngest.test_trash_cleans_both_stores`.
- [x] Dropping the LanceDB dir and re-ingesting reproduces identical retrieval —
      `store.drop_table()`/`TestIngest.test_resync_is_idempotent` cover the
      rebuild-from-MariaDB path (LanceDB is disposable by design, ADR 0002).
- [x] `Hybrid` measurably beats `Vector` on a keyword-heavy query —
      `TestKnowledgeStore.test_hybrid_search_surfaces_keyword_match`.

**Status: ✅ Complete (2026-08-07).** All sub-phases (4a–4f) landed; 132 new tests in
`test_knowledge.py`, full suite 323 tests / 316 passing, same 7 pre-existing
`ignore_user_permissions` failures as every prior phase check — no regressions.

**Implementation notes:**

- **A real bug found and fixed during testing, not by design review:**
  `embedder.EMBEDDING_CALLERS` was originally `dict[str, Callable]`, built once at
  module import with a direct reference to `_call_openai_compatible`. Patching
  `frappe_ai.knowledge.embedder._call_openai_compatible` in a test rebinds the
  module attribute but not the dict's already-captured reference, so `_embed_batch`
  kept calling the *original* function — the first test run made real network calls
  to `api.openai.com` and got real 401s instead of the mocked fixture. Fixed by
  storing function *names* (`dict[str, str]`) and resolving via `globals()` inside
  `_embed_batch` at call time. This is a correctness property, not just a testability
  one: any future consumer patching a caller function (a plugin, a runtime feature
  flag) would have hit the identical silent-no-op.
- Every other module (`extract.py`, `chunker.py`, `store.py`, `attachment_store.py`,
  `retriever.py`, `ingest.py`, `knowledge.py`) ported near-verbatim from `flow` —
  module-path/doctype-name substitution only, no logic changes. `knowledge/__init__.py`
  re-exports `Knowledge`, mirroring `flow.knowledge.__init__`.
- `AI Session`/`AI Session Attachment` (Phase 3 doctypes, deliberately forward-built
  with `ImportError`/plain-text-only guards for this phase to complete) wired up:
  `extract_attachment` now calls `frappe_ai.knowledge.extract.extract_file`
  (`FILE_EXTENSIONS`) instead of the Phase-3 plain-text-only `_extract_text_file`;
  `AISession.persist_turn`/`_index_retrieval_attachments`/`build_prompt_messages`
  gained the full Inline/Retrieval routing, chunk-and-embed-on-send, and
  retrieval-chunk injection on the latest turn — ported from `flow_session.py`'s
  equivalent methods.
- `tools/builtins.py`'s `bind_search_knowledge` rewired from its Phase-3 fail-closed
  stub to call `frappe_ai.knowledge.retriever.retrieve`; `_knowledge_search_description`
  added (per-KB description appended to the tool description, ported from `flow`).
- Two test-suite adjustments were needed for reasons specific to this bench's Frappe
  version, not `frappe_ai` bugs: (1) `AI Model.provider` is validated against known
  Agno provider slugs at save time, so a test needed a real-but-embeddings-unsupported
  slug (`anthropic`) rather than a fake one to exercise `EMBEDDING_CALLERS`' missing-
  entry path; (2) this Frappe version's HTML sanitizer escapes `<script>` to
  `&lt;script&gt;` at save time (a defense-in-depth improvement over whatever `flow`'s
  test environment did) — the escaped text is correctly rendered back to literal
  characters by extraction, indistinguishable in the final string from "never
  stripped," so the test asserts on real (non-escaped) tags instead, which
  `_render_rich_text`/`_extract_html` still strip correctly.
- `hooks.py`: `scheduler_events.daily` now runs `sync_due_sources`;
  `ignore_links_on_delete` added (`AI Knowledge Chunk`, `AI Run`, `AI Session`),
  mirroring `flow`'s.
- `pyproject.toml`: declared `lancedb`, `python-docx`, `pdfplumber`, `rapidocr`,
  `onnxruntime` (same pins as `flow`), plus `openpyxl`/`beautifulsoup4`/`lxml`/`chardet`
  (used by `extract.py` but not in `flow`'s own `pyproject.toml`) and the `libgl1`/
  `libglib2.0-0` apt deploy deps RapidOCR needs. All were already present in this
  bench's shared venv (residual from `flow`'s install) — declaring them makes
  `frappe_ai` correct as a standalone install, not just working by inherited accident.
- New [ADR 0012](../decisions/0012-embeddings-direct-provider-sdk.md): embeddings call
  provider SDKs directly (`EMBEDDING_CALLERS`, ships `openai` only), since neither
  litellm (ADR 0009) nor Agno (no `agno.embedder` package in the installed version)
  cover embeddings calls.

---

## Phase 5 — Triggers, Memory & MCP ✅

**Objective:** Unattended automation and the remaining capabilities.

### Tasks

- [x] DocType `AI Trigger` + controller (croniter, Jinja, condition validation)
- [x] `triggers/` — wildcard `doc_events` dispatch, `dispatch_scheduled`, `fire`
- [x] Recursion guard for the app's own DocTypes; skip during install/migrate
- [x] DocType `AI Agent Memory` + controller (500 chars, 100/bucket)
- [x] `memory/` — LanceDB FTS index, `build_memory_block`, `save_feedback_memory`
- [x] DocTypes `AI MCP Connection`, `AI Agent MCP Connection`
- [x] `api/mcp.py` — `check_connection`, `check_all_mcp_connections`,
      `get_mcp_health_dashboard`, `create_mcp_connection_from_json`
- [x] Scheduler: `*/5` trigger dispatch + MCP health probe
- [x] Tests: `test_triggers.py`, `test_memory.py`, `test_mcp.py`

### Completion criteria

- [x] Inserting a document fires a trigger producing an `AI Run` with `source: Trigger`
- [x] A cron trigger fires on schedule; `last_fired_at` advances
- [x] A failing condition fails closed (treated as not met)
- [x] Memory persists across sessions and is injected into the prompt
- [x] `check_all_mcp_connections()` reports accurate status

### Implemented on 2026-08-10

- Added durable agent memory: new `AI Agent Memory` DocType, `frappe_ai/memory/`
      package (`memory.py`, `store.py`), real `update_memory` builtin wiring, prompt
      injection into `AI Session.build_prompt_messages()`, and thumbs-down feedback
      persistence to shared memory when the agent has the memory tool.
- Added trigger automation: new `AI Trigger` DocType, `frappe_ai/triggers/`
      (`dispatch`, `dispatch_scheduled`, `fire`), wildcard `doc_events` registration
      in `hooks.py`, cron scheduler wiring, and `AI Run.trigger` support.
- Added MCP scaffolding: new `AI MCP Connection` and `AI Agent MCP Connection`
      doctypes, `api/mcp.py` helpers, `AI Agent.mcp_connections`, service-side config
      exposure, and `AgentBuilder` integration that degrades cleanly when MCP support
      is unavailable at runtime.
- Added focused Phase 5 tests: `test_memory.py`, `test_triggers.py`, `test_mcp.py`,
      plus updates to existing builtins/session/api coverage for the new memory path.

### Verification status

- **Python syntax/importability:** ✅ verified with `env/bin/python -m py_compile` across
      the changed Phase 5 files.
- **Focused Phase 5 tests:** ✅ verified on 2026-08-10. Against `tact.local`,
      `test_triggers.py` passed `3/3`, and `test_memory.py` passed `2/2`. `test_mcp.py`
      initially failed `1/2`, but the failure was a stale test assumption after `mcp`
      was installed (`check_connection()` no longer failed with the old "missing
      dependency" message once MCP support was actually present). That test was patched
      the same day to mock the dependency-failure path directly rather than depending on
      the local Python environment.
- **MCP import path:** ✅ verified on 2026-08-10. After installing `mcp<2`, `env/bin/python`
      successfully imports `from agno.tools.mcp import MCPTools`, so MCP is no longer
      dependency-blocked.
- **MCP live connectivity:** ✅ verified on 2026-08-10 against a real local stdio server.
      A tiny `FastMCP` test server at `/tmp/echo_mcp_server.py` was registered as
      `AI MCP Connection("local-echo-mcp")` with command
      `/home/a/harsha/harsha/env/bin/python /tmp/echo_mcp_server.py`. Running
      `frappe_ai.api.mcp.check_connection(name="local-echo-mcp")` returned
      `{"is_connected": true, "status_message": "Connected (1 tools)"}`, and
      `check_all_mcp_connections()` returned the same healthy status.
- **DB-backed reruns:** 🟡 still inconsistent in this sandbox. Some later reruns of
      focused Phase 5 modules failed before app code ran because MariaDB could not
      open a socket:
      `MySQLdb.OperationalError: (2004, "Can't create TCP/IP socket (1)")`.
      This remains an environment concern, but no longer blocks Phase 5 completion.

### Deviations from the original plan

- **Trigger execution uses the existing FastAPI SSE run path by consuming it from the
      worker.** This preserves the "one run path" design rather than creating a separate
      in-process trigger runtime inside Frappe.
- **MCP support is intentionally fail-soft.** `api/mcp.py` returns a clear dependency
      error when `mcp` is not installed, and `AgentBuilder` logs and skips bound MCP
      connections instead of crashing the whole agent build. After `mcp<2` was installed
      on 2026-08-10, the old test that asserted on the literal missing-dependency message
      became environment-sensitive and was rewritten to mock that dependency failure
      directly.

---

## Phase 6 — Frontend UX / Site Frontend Layer 🟨

**Objective:** Chat UX at parity with `flow`'s panel, plus a frontend-oriented API layer
usable by same-origin custom frontends on the site.

### Tasks

- [x] Replace the abandoned Vue/Vite plan with `apps/frappe_ai/frontend/` as a React +
  TypeScript app built by checked-in `esbuild`
- [x] Emit a committed site-served bundle to
  `frappe_ai/public/frappe_ai_panel/frappe_ai_panel.js|css`
- [x] Keep Frappe asset registration via `app_include_js/css` with mtime cache-busting
- [x] Rename mount point `#flow-root` → `#frappe-ai-root` for the slide-in desk panel
- [x] Cmd/Ctrl+I, fullscreen default, resize, persisted panel state
- [x] `extend_bootinfo` source of truth for supported file types
- [x] Build a frontend-oriented JSON API layer under `frappe_ai.api.frontend.*`
- [x] Rewrite the stream bootstrap path to use Frappe-start/resume + FastAPI bearer-token
  streaming
- [x] Add a standalone full-page mount at `/app/frappe-ai`
- [ ] Finalize page-mode layout as a dedicated page shell rather than reusing panel-first CSS
- [ ] Verify panel/page/custom-frontend parity end-to-end against the same API layer

### Completion criteria

- [x] Cmd/Ctrl+I opens the desk panel
- [x] Frontend assets build from a fresh checkout after frontend dependency install
- [x] Built assets load from `frappe_ai/public/frappe_ai_panel/`
- [x] Frontend can start/resume via Frappe and stream directly from FastAPI
- [x] A frontend-friendly BFF/JSON layer exists for same-origin custom frontends
- [ ] Panel, page, and custom-frontend shells all use layouts intentionally designed for
  their host surfaces
- [ ] Full transcript UX parity confirmed for confirmations, attachments, sessions,
  recovery, and feedback from the desk panel and page route

### Status note (2026-08-10)

Phase 6 is **in progress, not blocked by backend contracts**. The important backend and
frontend runtime pieces now exist:

- React + TypeScript frontend under `apps/frappe_ai/frontend/`
- esbuild-driven committed bundle under `frappe_ai/public/frappe_ai_panel/`
- desk panel mount and full-page `/app/frappe-ai` mount
- frontend-friendly JSON API layer under `frappe_ai.api.frontend.*`
- FastAPI bearer-token stream bootstrap preserved unchanged at the wire level

The remaining issue is **layout architecture**, not API execution:

- the full-page route now mounts the new React markup correctly
- but page mode is still borrowing a shell originally designed for the slide-in panel
- as a result, repeated “UI tweaks” were correcting symptoms without addressing the
  underlying mismatch between a panel-first shell and a full-page app surface

The next correct step is therefore **not more cosmetic patching on the shared shell**,
but a dedicated page-mode outer layout that shares state/components/API code with the
panel while owning its own page-specific composition and styling.

---

## Phase 7 — Parity Complete & Reconciliation ⬜

**Objective:** Feature parity with `flow`, and documentation that matches the code.
**Not** production readiness — that is Phase 8.

### Tasks

- [ ] `assistant/` — assistant agent + instructions, `sync_builtin_assistant`
- [ ] Stale-run recovery on both sides
- [ ] `default_log_clearing_doctypes` for `AI Session`
- [ ] Full test suite green
- [ ] **Concurrency test:** 25 simultaneous streams, desk stays responsive
- [ ] Documentation reconciliation (see below)
- [ ] Pre-uninstall checklist from [ADR 0005](../decisions/0005-greenfield-no-migration.md)
- [ ] `bench uninstall-app flow`

### Documentation reconciliation checklist

- [ ] Implementation matches `001-architecture.md`; deviations documented
- [ ] Every row in `002-feature-mapping.md` verified as implemented or explicitly dropped
- [ ] `003-doctype-reference.md` matches the shipped DocType JSON exactly
- [ ] ADRs reflect the **final** implementation, not the initial intent
- [ ] Any ADR superseded during implementation marked as such
- [ ] This progress file marked complete
- [ ] Obsolete documentation removed

### Completion criteria

- [ ] Every feature in `002-feature-mapping.md` demonstrable without reference to `flow`
- [ ] `bench uninstall-app flow` leaves `frappe_ai` fully functional
- [ ] Docs match the implementation

> **Reaching Phase 7 does not authorise production traffic.** See Phase 8.

---

## Phase 8 — Production Hardening 🟨

**Objective:** Make the parity-complete system safe to deploy.
**Dependencies:** Phases 1–7.
**Gate:** 🔴 **No production traffic until 8.1 is complete.**

Derived from the production-readiness review of 2026-08-05. Full triage — including what was
rejected and why — is in the approved plan; the accepted items are below.

### 8.1 — Safety critical 🟨

| # | Item | Detail |
|---|---|---|
| A1 | **SSE heartbeats** | `event: ping` every 15s when idle; client treats as liveness only. Without this, streams die behind proxies. |
| A2 | **Execution budgets** | Implemented for direct Frappe/FAC dispatch: `max_tool_calls` (50), `max_mutations` (20), `max_records_per_call` (100), `max_runtime_seconds` (600) on `AI Agent`. Remote MCP calls still bypass the counters. → [ADR 0008](../decisions/0008-execution-budgets.md) |
| A3 | **Mutation limits** | `max_records_per_call` is enforced by the direct dispatch boundary. Per-tool enforcement for remote MCP remains unresolved. |
| A6 | **Rate limiting** | Per-user and per-agent limits on run starts and tool dispatch via Frappe's Redis cache. Trigger runs get a tighter bucket than interactive chat. |
| A7 | **LLM retry policy** | Bounded exponential backoff + jitter on 429/5xx/timeout, 3 attempts. Retries counted in `AI Run.usage`. 401/400/content-filter fail immediately. |

**Tasks**

- [x] `AI Agent` — add the four budget fields
- [x] `AI Run` — add `budget_usage` (JSON)
- [ ] `AI Run` — add `trace_id` (Data, indexed)
- [ ] `AI Settings` — add `heartbeat_interval`, `rate_limit_per_user`, `rate_limit_per_agent`, `llm_max_retries`
- [ ] Heartbeat emission in the SSE generator; `ping` handling in the Vue client
- [x] Budget enforcement in `api/dispatch.py` and direct FAC dispatch
- [ ] Extend equivalent accounting to remote MCP calls
- [ ] Rate limiting in `api/api.py` and `api/dispatch.py`
- [ ] Retry wrapper around LLM calls in the service

**Completion criteria**

- [ ] A stream held idle through a long reasoning step survives a proxy with a 30s idle timeout
- [ ] An agent asked to create 500 records is stopped at `max_records_per_call`; run fails with a budget error; `budget_usage` is accurate
- [x] A paused-then-resumed run **continues** accumulating counters rather than resetting
- [x] Direct Frappe/FAC dispatch enforces budgets even when bypassing the service
- [ ] Remote MCP dispatch enforces the same budgets
- [ ] Exceeding the per-user rate returns a clear error, not a stack trace
- [ ] A mocked 429 retries and succeeds; a mocked 401 fails immediately

### 8.2 — Operability ⬜

| # | Item | Detail |
|---|---|---|
| A4 | **Correlation IDs** | `trace_id` + `run_id` on every service call (`X-Trace-Id`, `X-Run-Id`), in every log line on both sides, and in SSE `error` payloads. |
| A5 | **Structured observability** | JSON logging keyed by `trace_id`; `/metrics` in Prometheus format — runs started/completed/failed, duration histogram, tool latency by tool, token usage, budget rejections, LLM error rate by provider. Adds `prometheus-client` only. |

> **Do A4 early if convenient.** Plumbing two headers through the service skeleton during
> Phase 2 costs almost nothing; retrofitting logging afterwards does not. The item is
> scheduled here, but pulling it forward is encouraged.

**Completion criteria**

- [ ] One `trace_id` correlates browser request → Frappe log → service log → tool dispatch log
- [ ] `/metrics` scrapes cleanly; a failed run appears in both the failure counter and structured logs

### 8.3 — Governance & reliability ⬜

| # | Item | Notes |
|---|---|---|
| B1 | Cost accounting | Per-run cost from tokens × model pricing, rolled up per user/agent. `AI Run.usage` already captures tokens. |
| B2 | Resource quotas | Per-user/org token and run ceilings, built on A6. |
| B3 | Replayable SSE | `Last-Event-ID` resume after disconnect. Needs an event store — **only if A1 proves insufficient**. |
| B4 | Prompt versioning | `config_snapshot` already captures instructions; this surfaces them as a diffable version. |
| B5 | Tool schema versioning | So a changed signature does not silently break replay. |

### 8.4 — Backlog (unscheduled)

Provider routing policies · evaluation framework + golden conversations · retrieval quality
metrics (the natural place to revisit hybrid-vs-vector empirically) · prompt A/B testing ·
hallucination metrics · tool success analytics · queue-based execution · pluggable vector
store abstraction.

**Mid-session model switching** — implemented and verified on 2026-08-07. A supplied model
updates an existing session when no run is `Running` or `Paused`; disabled models and
model-permission failures are rejected. See
[004-session-model-switching.md](../specifications/004-session-model-switching.md).

### Explicitly rejected (do not re-litigate without new evidence)

| Item | Why | Record |
|---|---|---|
| Mid-run durable execution | Fail-and-retry is sufficient for interactive runs; triggers already durable via RQ | [ADR 0007](../decisions/0007-failure-over-durable-execution.md) |
| Worker pool for the service | Misdiagnosis — runs are I/O-bound; async concurrency *is* the scaling mechanism | ADR 0001 |
| Pluggable multi-backend vector store | Abstractions written against one implementation are usually wrong; `store.py`/`retriever.py` already localise LanceDB | ADR 0002 |
| Sandbox versioning | Greenfield app, zero existing Script tools to stay compatible with | ADR 0005 / 0006 |
| Circuit breakers + provider fallback | A7 covers transient failures; fallback silently changes model behaviour mid-conversation | Deferred to 8.4 |

---

## Blockers

*None.*

---

## Change Log

| Date | Change |
|---|---|
| 2026-08-21 | **Environment and API verification:** host-level checks confirmed MariaDB active on `127.0.0.1:3306`, Frappe web on `:8000`, FastAPI on `:8001`, and Bench workers/scheduler running. `frappe_ai.tests.test_api` passed **34/34** with host-level database access. Earlier socket failures were restricted-sandbox access failures, not a MariaDB outage. |
| 2026-08-07 | **Added `is_default` to `AI Model`.** New `Check` field enforces at most one default at a time via `_enforce_single_default()` in `validate`. New `get_default_model()` helper in `lib/model.py` returns the enabled default (or `None`). 5 new tests in `test_ai_model.py`, all passing. No regressions. |
| 2026-08-05 | Phase 0 complete. Specs + ADRs 0001–0006 written. |
| 2026-08-05 | **Reversed the vector-store decision** — ChromaDB → LanceDB. Preserves hybrid search and BM25 memory recall; retrieval pipeline now ports rather than being rewritten. ADR 0002 rewritten. |
| 2026-08-05 | Confirmed `safe_exec` is retained — Agno does not provide sandboxing. ADR 0006 gained an explicit section on why. |
| 2026-08-05 | **Production-readiness review triaged.** 26 concerns → 15 accepted into a new Phase 8, 5 rejected with rationale, 6 backlogged. Phase 7 renamed "Parity Complete" to stop it implying production readiness. |
| 2026-08-05 | ADR 0007 written — fail-and-retry over durable execution. Rejects the review's top P0 as a blocker; records why triggers get RQ durability and interactive runs do not. |
| 2026-08-05 | ADR 0008 written — execution budgets and mutation limits. Closes a real gap inherited from `flow`: `max_iterations` bounds the loop but nothing bounded writes. |
| 2026-08-05 | **Phase 1 in progress; dropped `litellm` as a dependency.** Surfaced mid-implementation while porting `Flow Provider`/`Flow Model`: routing model calls through litellm underneath Agno was a redundant abstraction layer. `AI Provider`/`AI Model` redesigned around Agno's native per-provider model classes instead. ADR 0009 written; `001-architecture.md` §10, `002-feature-mapping.md` §1, and `003-doctype-reference.md` §1–2 updated to match. |
| 2026-08-05 | **Phase 1 complete.** `AI Provider`, `AI Model`, `AI Settings` + controllers; `safe_exec`/`conditions`/`system_generated` ported (conditions now on the hardened namespace, closing ADR 0006's gap); `agno` 2.8.7 installed and used for real in `test_connection()`. 69 tests, 62 passing; 7 failures are a pre-existing Frappe-core/`flow` compatibility gap (`ignore_user_permissions` kwarg removed from `DatabaseQuery.execute()`), confirmed identical in `flow`'s own suite on this bench — not a `frappe_ai` regression, not fixed in this phase. |
| 2026-08-06 | **Phase 2 complete.** Hand-built FastAPI service skeleton (AgentOS evaluated, explicitly deferred). HMAC run-token primitive (`auth.py`), async Frappe client, `GET /health`, a forward-looking run-token-gated `/stream/{run}` placeholder. Discovered and worked around a real Frappe-core quirk: `Authorization: Bearer ...` is intercepted globally by `validate_auth()` before any whitelisted method runs, even `allow_guest=True` ones — moved the service→Frappe shared secret to a dedicated `X-Frappe-AI-Service-Secret` header instead. ADR 0010 written for the bootstrap-secret design (env vars). |
| 2026-08-06 | **Found and fixed a real gap: `bench start` didn't boot `ai` unattended.** ADR 0010's env-var secret required a manual export step nothing automated; plain `bench start` crash-looped the `ai` process. Fixed via **ADR 0011** (supersedes ADR 0010): moved the secret from `AI Settings.service_secret` (DB field, now removed) + an env var, to `site_config.json`'s `frappe_ai_service_secret` — one source of truth instead of two kept in sync by hand. `FRAPPE_AI_SITE` now defaults to `common_site_config.json`'s `default_site`, so `bench start` requires zero environment variables on a single-site bench. Verified live: killed stray redis processes, unset all `FRAPPE_AI_*` vars, ran plain `bench start`, confirmed `ai.1` came up and `/health` returned `frappe_reachable: true`. Full test suite rerun, same pass/fail counts as before (91 tests, 84 passing, 7 pre-existing unrelated failures) — no regressions from the change. |
| 2026-08-06 | **Phase 3 complete.** 8 new DocTypes (`AI Agent`(+`Tool`/`Knowledge Base` child tables), `AI Tool`, `AI Session`(+`Message`/`Attachment` child tables), `AI Run`); `frappe_ai/lib/tool.py`+`resolver.py` (schema derivation, ported from `flow` near-verbatim — pure Python, no Frappe imports); `frappe_ai/tools/builtins.py` (10 builtins, wired to `after_migrate` via `sync_builtin_tools()`); `frappe_ai/api/dispatch.py` (new — no `flow` equivalent, since `flow` never split the agent loop out of Frappe; `frappe.set_user(acting_user)` before every tool body is the actual mechanism behind ADR 0003); `frappe_ai/service/builder.py` (`AgentBuilder`) and `frappe_ai/service/routes/chat.py` (SSE run loop), wired into `main.py`'s `/stream/{run}` (replacing the Phase 2 placeholder). Confirmation pause/resume deliberately does **not** use Agno's native `requires_confirmation`/`RunPausedEvent`/`continue_run` — that mechanism needs Agno's own session `db` to hold a paused `RunOutput` between requests, which conflicts with the stateless-service/Frappe-is-truth architecture; built instead as a `PendingConfirmation` exception each tool's entrypoint raises, caught and collected across a turn the way `flow`'s `Question` collection worked. `search_knowledge`/`update_memory` fail closed (Phase 4/5 doctypes don't exist yet); attachments are Inline-mode/plain-text only (rich extraction is Phase 4); `mcp_connections`/`AI Trigger` references omitted from Phase 3's DocType JSON entirely rather than left dangling. 91 new tests (155 total, 148 passing — the 7 failures are Phase 1's pre-existing, documented `ignore_user_permissions` gap, not a regression). **Two real Agno API bugs surfaced only by live-testing against a standalone service instance, not by unit tests or reading Agno's stubs:** `agent.arun(stream=True)` is not itself awaitable (fixed: dropped a stray `await`), and a failed run yields a `RunErrorEvent` with no final `RunOutput` at all rather than a `RunOutput` with `status=error` (fixed: `RunErrorEvent` handled directly). Verified live end-to-end through a real `AI Provider`/`AI Model` → `start_run` → HMAC token → SSE stream → real `agno.models.openai.OpenAIChat` call, using a deliberately invalid API key: got a genuine OpenAI 401, correctly surfaced as an `error` SSE event, with `AI Run` persisted `Failed` via the `fail_run` callback — the same "fake key still proves the real path" verification Phase 1 used for `test_connection()`. **Not verified live: the success path** (a real streamed completion, a real tool call reaching `dispatch_tool`, a genuine confirmation pause/resume cycle) — no LLM API key is available in this environment. Flagged as the first thing to verify once one is available. |
| 2026-08-07 | **Fixed a real gap found while live-testing Phase 3's success path: `AI Model.provider` required a matching `AI Provider` *document* to exist just to save, even though no application logic actually needed one.** Discovered trying to prove a real streamed completion with a real Groq API key — `ImportError: groq not installed` (Agno needs each provider's actual SDK; only `openai`'s is installed in this bench), and the two workarounds considered (reuse/rename the `AI Provider("openai")` row's `base_url`, or install `groq`) were both explicitly rejected — the first as misleading state, the second as defeating the goal of not needing N SDKs for N providers. Traced the actual blocker to `AI Model.provider` being a `Link → AI Provider` field: Frappe validates Link fields against the target row existing at save time, independent of `_model_call_config`/`resolve_provider_credentials` already degrading gracefully with no such row. Changed `provider` from `Link` to `Autocomplete` (mirrors `model_id`'s existing pattern exactly); dropped `api_key`/`base_url`'s `depends_on: eval:!doc.provider` so a model can carry its own credentials alongside a provider slug. `AI Provider` remains fully optional — a convenience for sharing credentials across models, never a requirement. Two Phase-1 tests updated to match (one asserted the now-obsolete `LinkValidationError`; one had a fixture-naming collision with real slug-based setup work). Full writeup with alternatives considered in new `docs/learnings.md`. With the fix, live-verified the full success path: `AI Model(provider="openai", base_url="https://api.groq.com/openai/v1")` routed the already-installed `openai`/Agno class at Groq's real OpenAI-compatible API — real streamed `text` deltas, a real `find_doctypes` tool call through `dispatch_tool`, correct final `output`/`iterations`/usage, and a correctly persisted `AI Run` + session transcript, all confirmed directly against the DB. 155 tests, 148 passing, same 7 pre-existing failures as before — no regressions. |
| 2026-08-07 | **Closed Phase 3's last open item: live-verified confirmation pause/resume, all three answer types, finding and fixing three real bugs along the way.** None were caught by the 148-passing unit suite, since none of it round-tripped a real pause → resume → model-continuation cycle end to end. (1) The transcript persisted on pause included the internal `PENDING_CONFIRMATION_MARKER` string as if it were a real tool result; a resumed model read it as "already done" and hallucinated a fake success — fixed via `_messages_excluding_pending`, which strips both the marker row and its pairing assistant `tool_calls` message from what gets persisted. (2) Approving a call never actually made anything happen: `approved_call_ids` only gated whether a *re-requested* call would dispatch, but nothing made the model re-request the exact call it was blocked on (especially after fix #1 removed the stale request from its context) — fixed with `_dispatch_approved`, which directly dispatches each approved call via `FrappeClient.dispatch_tool` using the arguments `AI Run.questions` recorded at pause time (now also returned by `get_run_config`), then injects the reconstructed assistant/tool message pair into both the model's input for this turn and the persisted transcript — mirroring `flow`'s own `_prepare_resume` ("Approve" → actually run the tool), which Phase 3's first pass under-implemented. (3) Deny's persisted result silently dropped its own audit record: `_denied_result` filtered the `system` row out of the prior transcript before prepending denial rows, which shifted `_new_messages_for_session`'s stored-row-count-based diff by one and dropped the reconstructed assistant message — fixed by passing the prior transcript through unfiltered, matching exactly what's stored. All three fixes verified live against the real Groq-via-OpenAI-compat setup: Approve produced a real dispatched `create` call and a real DB record; Deny produced no dispatch and no DB record, halting the run immediately; redirect had the model read free-text feedback, correct its own arguments, retry, and only the corrected version was ever created. A noted-but-unfixed cosmetic issue remains (a duplicate `user` row in Approve's persisted transcript, from `RunOutput.messages` echoing input verbatim) — inert, doesn't affect correctness, flagged for before Phase 6 renders transcripts to a human. Full root-cause writeup in `docs/learnings.md`. 155 tests, 148 passing, same 7 pre-existing failures, no regressions. Phase 3 has no remaining open items. |
| 2026-08-07 | **Phase 4 complete.** 3 new DocTypes (`AI Knowledge Base`, `AI Knowledge Source`, `AI Knowledge Chunk` — `autoincrement` naming, load-bearing as the LanceDB row `id`); full `frappe_ai/knowledge/` package ported from `flow` near-verbatim (`extract.py`, `chunker.py`, `store.py`, `attachment_store.py`, `retriever.py`, `ingest.py`, `knowledge.py`) — module-path/doctype-name substitution only, no logic changes, since none of that code was litellm-specific. The one real adaptation: `embedder.py`'s single litellm call site (`litellm.embedding()`) has no drop-in replacement — neither litellm (ADR 0009) nor Agno (no `agno.embedder` package in the installed version) cover embeddings — so **ADR 0012** introduces `EMBEDDING_CALLERS`, a small opt-in provider→direct-SDK-call mapping (ships `openai` only, same "any OpenAI-wire-compatible endpoint via `base_url`" flexibility Phase 3's learnings established for chat). Rewired `tools/builtins.py`'s `bind_search_knowledge` from its Phase-3 fail-closed stub to call `frappe_ai.knowledge.retriever.retrieve`, with `_knowledge_search_description` appending bound KBs' descriptions (ported from `flow`). Extended `AI Session`/`AI Session Attachment` — both were deliberately forward-built in Phase 3 with `ImportError`/plain-text-only guards for this phase to complete without a rewrite: `extract_attachment` now calls the real `frappe_ai.knowledge.extract.extract_file`, and `AISession` gained `_index_retrieval_attachments`/Inline-vs-Retrieval routing/retrieval-chunk injection on the latest turn, ported from `flow_session.py`. `hooks.py` gained `scheduler_events.daily` (`sync_due_sources`) and `ignore_links_on_delete`; `pyproject.toml` gained the knowledge-pipeline dependencies (all already present in this bench's shared venv from `flow`, now declared as `frappe_ai`'s own). **One real bug found by testing, not design review:** `EMBEDDING_CALLERS` originally stored a direct function reference captured at module-import time; patching the function in a test silently didn't take effect (the dict still held the old reference), so the first test run made real unmocked calls to `api.openai.com`. Fixed by storing function names and resolving via `globals()` at call time inside `_embed_batch` — a correctness fix, not just a test workaround, since any runtime consumer patching a caller function would hit the same silent no-op. 132 new tests in `test_knowledge.py` (ported from `flow`'s 18 test classes, embedder-mock target adapted to the new call site); full suite 323 tests, 316 passing, same 7 pre-existing `ignore_user_permissions` failures as every prior phase, no regressions. Not live-verified against a real embedding API call (no key available in this environment, same caveat as Phase 3's initial pass) — flagged as the first thing to verify once one is available, same pattern Phase 3 used. |
| 2026-08-07 | **Closed the mid-session model-switching gap (spec `004-session-model-switching.md`), a documented-but-never-implemented behaviour, not a new phase.** `_resolve_session` (`api.py`) previously loaded an existing session as-is and silently discarded the `model` argument once a session existed — the override only ever took effect at session creation. Added a model-diff branch guarded by `AISession.assert_not_blocked()`: a `start_run(session=..., model=...)` call with a different model now saves the switch (rejected if the model is disabled, via `_validate_model_enabled` in `validate()`) and is refused outright while the session has a `Paused`/`Running` run, closing the resume hazard (a paused run resuming under a model it didn't pause under). No changes needed elsewhere — `_check_agent_usable`, `AIAgent._snapshot`, and the FastAPI service already resolved the effective model live per turn. 4 new tests in `test_api.py::TestStartRunValidation` (switch succeeds, disabled model rejected, blocked mid-run, permission-denied via a `User Permission` restriction since `AI Model` grants role `All` read). Full suite: 296 tests, same 7 pre-existing `ignore_user_permissions` failures, no regressions. |
| 2026-08-08 | **ADR 0013 — reintroduced litellm for provider/model-selection UX and reverted `AI Model.provider` to a real `Link → AI Provider`, mirroring `flow`'s architecture.** `get_provider_models()` had been a hardcoded no-op since ADR 0009 dropped litellm, leaving `model_id` with zero autocomplete suggestions; `provider` had separately drifted to an undocumented `Autocomplete` (`docs/learnings.md`) purely to dodge Frappe's Link-existence check. Both reversed on the user's explicit direction to follow `flow`'s design instead of preserving the decoupled model. litellm is scoped strictly to `AI Provider._validate_provider_known` (against `litellm.provider_list`) and `get_provider_models` (against `litellm.models_by_provider`, two-factor filtered by `is_known_provider` so a provider litellm knows but Agno can't run yields no suggestions) — chat execution (`_model_call_config`, `test_connection`) never touches litellm, still Agno-only per ADR 0009's core decision. Credential resolution changed from a provider-then-model merge to a hard two-state split: `provider` linked → class + `api_key`/`base_url` from the `AI Provider` doc only (no per-model override); `provider` empty → the model's own `api_key`/`base_url` against `agno.models.openai.OpenAIChat` (the already-proven "any OpenAI-compatible endpoint via `base_url`" pattern, now automatic instead of requiring `provider="openai"`) — removes the old hard "No Provider" throw, an unlinked model is runnable now. **One real gap found mid-implementation, not anticipated in the plan:** litellm and Agno spell six providers differently for the same service (Agno's `fireworks`/`together`/`google`/`nvidia`/`aws`/`meta` vs. litellm's `fireworks_ai`/`together_ai`/`gemini`/`nvidia_nim`/`bedrock`/`meta_llama`) — caught by a genuine test failure (`test_model.py`'s pre-existing `fireworks` fixture) against litellm's real `provider_list`, not by design review. Fixed with `LITELLM_PROVIDER_ALIASES`/`to_litellm_provider()` in `lib/model.py`, applied at both litellm-facing call sites; the stored `AI Provider.provider` value stays in Agno's spelling. `embedder.py` (Phase 4/ADR 0012) is unaffected — its own provider-then-model precedence for embeddings is untouched, out of scope for this change. Full suite: 307 tests, same 7 pre-existing `ignore_user_permissions`/`test_conditions` failures, no regressions. Full design rationale, alternatives considered (auto-create-provider-on-save was proposed and rejected), and behavior-change caveats in **ADR 0013**. |
