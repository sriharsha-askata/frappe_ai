# End-to-end harness

Runs the app on a real bench (Frappe web, worker, the FastAPI service) against **MariaDB and
Postgres**, with a stub standing in for the LLM and the embedding model. See
`docs/progress/e2e-validation.md` for what it covers and the latest results.

| File | Purpose |
|---|---|
| `stubs/openai_stub.py` | OpenAI-compatible server: deterministic embeddings and a scripted chat model (`[[search: ...]]`, `[[tool: name {json}]]`, `[[fail]]`) |
| `run_in_site.py` | Run `module.function` inside a bench site as Administrator (what a background job would do) |
| `e2e_checks.py` | The site-side functions: seeding, inspection, and the install/registry/pgvector checks |
| `run_scenarios.py` | The HTTP scenarios: real API calls, real SSE stream, state read back from the site |
| `services.sh`, `start_stack.sh` | Start MariaDB/Redis/Postgres, then web, worker and one FastAPI service per site |
| `b.sh`, `r.sh`, `scn.sh` | Small wrappers: run `bench`, run a site function, run the scenarios |

Paths in the scripts assume the layout used when the harness was built (`/var/tmp/e2e`, a bench in
`/var/tmp/e2e/work/frappe-bench`, a non-root `frappe` user, sites `maria.local`/`pg.local` plus
`tmaria.local`/`tpg.local` for the app's own test suite). Adjust them for your machine.

## Requirements learned while building it
* Frappe v16 (needs Python 3.14 and Node 24); the app's tests use `frappe.tests.IntegrationTestCase`, which v15 does not have.
* Install Assistant Core first (`buildswithpaul/Frappe_Assistant_Core`, tag v3.3.0 was used), then this app.
* The app no longer depends on `litellm` (ADR 0023); the earlier `litellm`/`openai` conflict that blocked `pip install` is gone, and `pip check` is clean. The Chroma client and the `psycopg` test driver are the optional extras `chroma` and `dev`.
* Postgres sites need `CREATE EXTENSION vector;` run by a privileged role.
* Run the scenario runner as root (it calls `r.sh`, which switches to the bench user); give the bench user a readable copy of the CA bundle if you use a proxy.

## Running
```bash
./services.sh && ./start_stack.sh
./scn.sh maria.local 8001          # all scenarios
./scn.sh pg.local 8002 --only approval triggers
```
