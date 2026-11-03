# End-to-end validation: MariaDB and Postgres

Status as of 2026-11-03, branch `feat/db-independent-vector-store`. Everything here was run, not
read: a real bench with the app installed, the web server, a background worker, the FastAPI service,
and a stub model. The harness is in `dev/e2e/` and can be re-run.

## Environment

| Part | Version |
|---|---|
| Frappe | 16.54.0 (`version-16`), Python 3.14.6, Node 24 |
| Assistant Core | v3.3.0 |
| MariaDB | 10.11.14 |
| Postgres / pgvector | 16.15 / 0.6.0 |
| Agno / openai / LanceDB / Chroma | 2.9.0 / 1.109.1 / 0.41.0 / 1.5.9 |
| LLM and embeddings | local stub (`dev/e2e/stubs/openai_stub.py`), not a real model |

Frappe v16 is required by the tests (`IntegrationTestCase`); v15 does not have it. Frappe labels its
Postgres support "experimental" in v16.

## Results

**App test suite** (`bench run-tests --app frappe_ai`, fresh sites): 402 tests on each database.
The same two tests error on both: they need FAC tools (`persist_spec_review_result`,
`match_historical_enquiries`) that belong to another app. They fail identically on the code before
this branch. The vector contract suite (187 tests, pgvector against real Postgres, LanceDB, Chroma)
passes with nothing skipped.

**Install and migrate:** the app installs on both databases, `bench migrate` is repeatable, the four
native tools reach Assistant Core's registry, and the Error Log stays clean.

**End-to-end scenarios** (real HTTP API, real SSE stream, state read back from the site):

| Scenario | MariaDB + LanceDB | Postgres + pgvector |
|---|---|---|
| Ingest through the worker; index ids equal database ids | pass | pass |
| Chat run that searches knowledge; transcript and usage saved | pass | pass |
| Approval: pause, approve once, memory written once, replay refused | pass | pass |
| Deny; stop is final and idempotent; provider error marks the run Failed | pass | pass |
| Backend switch and rebuild (lancedb, chroma; pgvector on Postgres); unknown backend rejected | pass | pass |
| Memory: 31 memories, the relevant one reaches the model's system prompt | pass | pass |
| Triggers: document event, scheduled; two processes race for one window, one wins; no double fire | pass | pass |
| Large attachment: retrieved instead of inlined; chunks removed with the session | pass | pass |
| 8 parallel tool calls on one run, each counted | pass | pass |
| `%` and `_` in session search matched literally | pass | pass |

## Defects found by running it, and fixed

1. **pgvector through `frappe.db.sql`** failed: Frappe turns list parameters into tuples, which Postgres
   reads as a row (`cannot cast type record to text[]`). Array filters are now passed as array literals.
2. **Ingestion crashed** with `'int' object has no attribute 'chunks'`: a loop variable named `index`
   shadowed the index module. Renamed.
3. **JSON fields are already parsed on Postgres** (strings on MariaDB). Several `json.loads` calls and
   tests assumed strings. They now use `frappe.parse_json`.
4. **pgvector DDL committed the surrounding transaction** (Frappe's `sql_ddl` commits first), which also
   broke test isolation. DDL now runs inside the transaction and rolls back with it.
5. **Postgres concurrency:** Frappe runs Postgres at REPEATABLE READ, so two requests locking the same
   run row make the second fail with a serialization error instead of waiting. Eight parallel tool calls
   gave seven HTTP 500s and a budget count of 1. `retry_on_conflict` now re-runs dispatch,
   `persist_run_result`, `fail_run` and `stop_run`; the scheduled-trigger claim treats a conflict as
   "already claimed". After the fix all eight calls succeed and are counted.
6. **`count(*) as total`** is rejected by Frappe v16 (dict syntax required), and Postgres needs an empty
   `order_by` for aggregates. Fixed in `safe_exec`.

## Observations that are not caused by this branch

* **Fresh install was blocked by the dependency pins (since fixed):** `litellm>=1.83.0,<1.83.8` needs
  `openai>=2`, but the app pins `openai>=1.0,<2`; and litellm 1.83.x also downgrades `click` and
  `python-dotenv` below what Frappe v16 requires. The app only used litellm for provider-name checks and
  model suggestions, so it was removed (ADR 0023). `pip check` is now clean and the suite and scenarios
  pass without it (`agno 2.9.0`, `openai 1.109.1`).
* **Attachment routing ignores the model's context window:** `persist_turn` calls
  `_attachment_inline_threshold()` without a snapshot, so the 128k default applies. A 113k-character file
  was inlined whole for a model configured with 8k tokens.
* **Console and `bench execute` do not get the four native tools** until the `before_request` / `before_job`
  hook runs; web requests and jobs are fine.
* **LanceDB and Chroma are not transactional:** a failure right after the index write leaves an orphan
  vector (seen: 1). Retrieval ignores it because chunks are read back from the database, and a rebuild
  clears it. pgvector leaves none.
* **Streaming is not live.** With the stub emitting a word every 0.25 s, `run_started` reached the client
  at 0.01 s, but every `text` frame arrived together at 1.93 s and `done` at 2.0 s. The service buffers the
  whole turn before it sends the frames (this is what makes the fallback-model retry safe), so the user
  sees nothing until the model has finished.

## Not tested

A real model or Ollama (retrieval *quality* is untested; the stub's embeddings only prove the plumbing),
MinIO, the browser UI, production load, Postgres with an unprivileged role that cannot create the
extension at run time beyond the error message check, and upgrading a site created on the previous
branch (this branch assumes fresh installs).
