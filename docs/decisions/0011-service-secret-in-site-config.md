# ADR 0011 — Service secret lives in `site_config.json`, not `AI Settings` + an env var

**Status:** Accepted
**Date:** 2026-08-06
**Deciders:** Sri Harsha Dabbiru
**Supersedes:** [ADR 0010](0010-service-bootstrap-via-env-vars.md)
**Prompted by:** A gap found after Phase 2 shipped — `bench start` alone did not
boot the `ai` process; it required `FRAPPE_AI_SERVICE_SECRET` exported by hand
first, which nothing automated.

---

## Context

ADR 0010 resolved the bootstrap chicken-and-egg problem (the service's first call
to Frappe must already be authenticated, so the secret can't come from that call)
by putting the secret in two places: `AI Settings.service_secret` (a `Password`
field, DB-stored, authoritative on the Frappe side) and `FRAPPE_AI_SERVICE_SECRET`
(an environment variable the service reads at startup), with the expectation that
whoever deploys the service keeps the two in sync by hand.

In practice, on this bench, nobody set the environment variable. Running plain
`bench start` produced:

```
ai.1 | frappe_ai.service.config.ServiceConfigError: FRAPPE_AI_SERVICE_SECRET environment variable is required
system | ai.1 stopped (rc=1)
```

`web`, `worker`, and the rest of the Procfile were unaffected — only `ai` crash-
looped, silently, because the Procfile's own comment documenting the requirement
is easy to miss and nothing enforces it.

The deeper issue ADR 0010 didn't fully reckon with: **duplicating a secret across
a database field and a process environment variable, with manual sync as the only
mechanism holding them together, is a design that fails silently by default.**
There is no error, no warning, no drift detection — just a crashed process (best
case) or a service running with a stale secret that quietly stops authenticating
correctly (worse case, if the DB value is rotated and the env var isn't).

`AI Settings.service_secret` being a DB field also means every other Frappe
process (web workers, background workers, `bench console`) that might reasonably
want to read this value has to go through `frappe.get_cached_doc("AI Settings")` —
extra ceremony for what is, functionally, deployment configuration rather than
business data.

---

## Decision

**The service secret lives in `site_config.json` as `frappe_ai_service_secret` —
one file, already the single source of truth every Frappe process (web, worker,
console, and now the FastAPI service) reads for exactly this kind of
per-site/per-deployment configuration.** `AI Settings.service_secret` is removed
from the DocType entirely; there is no longer a second copy to drift out of sync.

- **Frappe-side** (`frappe_ai/api/service.py`): reads `frappe.conf.frappe_ai_service_secret`
  — the same mechanism every other piece of Frappe config uses (`db_password`,
  `encryption_key`, etc. all live in the same file already).
- **FastAPI-side** (`frappe_ai/service/config.py`): reads `site_config.json`
  **directly off disk as plain JSON**, merged with `common_site_config.json` the
  same way `frappe.config.get_site_config()` does — **without** calling
  `frappe.init`/`frappe.connect`. This is a deliberate, narrow exception: the
  service still never opens a database connection, never imports `frappe`, and
  still authenticates every subsequent call over HTTP exactly as before. Reading
  one static file at process startup is not the same operation as connecting to
  MariaDB, and does not reintroduce the coupling `001-architecture.md` §10's
  "independently deployable and horizontally scalable" claim is actually about —
  that claim concerns not needing a live Frappe process or DB connection to run
  or to scale horizontally, which still holds: multiple `ai` instances can each
  read the same file independently, with no coordination, no live dependency on
  Frappe being up.
- **The only thing still supplied externally at process launch is the site
  name** (`FRAPPE_AI_SITE`), and even that now has a fallback: if unset, the
  service reads `default_site` from `common_site_config.json` — the same value
  `bench start` itself uses when no site is specified. On this bench
  (`default_site: "tact.local"`), the service now boots under plain `bench
  start` with **zero** required environment variables.

`ADR 0010`'s rejection of "service reads `site_config.json` directly" is
superseded specifically because its stated reason no longer holds: it argued
`site_config.json` "does not contain `AI Settings.service_secret` (that lives in
the database)" — true when written, no longer true, because this ADR moves the
secret into that file specifically to fix the problem ADR 0010's own env-var
design created.

---

## Consequences

### Positive

- **`bench start` now boots the `ai` process unattended** — the gap this ADR
  exists to close. No manual export step, nothing to forget.
- **One source of truth, not two.** There is no longer a "keep the DB value and
  the env var in sync" step for anyone to skip.
- **Consistent with how every other piece of Frappe deployment config already
  works** — `site_config.json` already holds `db_password`, `encryption_key`,
  and similar per-site secrets; this is one more entry in an existing, familiar
  pattern, not a new mechanism.
- **Removes a DB round-trip and a `Password` field's encrypt/decrypt overhead**
  from the hot path of every service→Frappe call's auth check — `frappe.conf` is
  loaded once per process, not queried per request.

### Negative

- **The secret is now a plaintext value on disk** (`site_config.json` is not
  encrypted at rest the way a DocType `Password` field is — Frappe's field-level
  encryption doesn't apply to config file values). This is consistent with how
  `db_password` already sits in the same file, so it does not introduce a new
  category of risk to this deployment, but it is a real reduction in
  at-rest protection compared to ADR 0010's DB-stored secret, worth stating
  plainly rather than glossing over.
- **Rotating the secret now requires editing a file on the Frappe host**, not
  just saving a DocType — mildly less convenient for a System Manager doing it
  from the desk UI, though also arguably more appropriate for a value that is
  infrastructure configuration, not application data.
- **The FastAPI service now has a narrow, direct filesystem dependency** on the
  bench's `sites/` layout (`_bench_root()` in `config.py` derives the bench root
  from its own install path). A deployment that moves the service to a different
  host entirely (not just a different process on the same host) would need that
  path made explicit rather than inferred — `FRAPPE_AI_SITES_PATH` is provided
  as an escape hatch for exactly this.

### Neutral

- `AI Settings.service_base_url`, `request_timeout`, `stream_timeout`,
  `lancedb_path`, `service_status` are unaffected — still DB fields, still
  fetched lazily over HTTP via `get_service_config`, unchanged by this ADR.
- The wire-level auth mechanism (`X-Frappe-AI-Service-Secret` header, not
  `Authorization`) is unchanged — that was never about *where* the secret is
  stored, only how it's carried on the request.

---

## Alternatives Considered

### Keep ADR 0010's design; just document the env-var requirement more loudly
Rejected. The requirement was already documented — in the Procfile itself, in
ADR 0010, in the progress tracker. The failure mode wasn't a documentation gap;
it was a design that depends on a manual step with no enforcement and no
fallback. More documentation does not fix a single point of manual, unenforced
synchronization.

### A bench-root `.env` file, auto-loaded by the Procfile runner
Considered seriously — this was one of the options raised when the gap was first
found. Rejected in favor of `site_config.json` because it would still be a
*second* place to configure the secret (the `.env` file, alongside whatever
`AI Settings` or site config already holds other deployment values), rather than
consolidating onto the one file every other piece of Frappe deployment
configuration already uses. It also doesn't naturally extend to multi-site
benches the way per-site `site_config.json` does — a bench-root `.env` has no
per-site scoping without inventing one.

### A wrapper script that derives the secret from a live site's `AI Settings` at Procfile-invocation time
Rejected, as it was when first raised. Would require the process launching
`uvicorn` to itself have a working Frappe context (`bench --site X console` or
equivalent) just to read one value before handing off to a process that
otherwise has none — reintroducing exactly the `frappe.init`/`connect` dependency
`001-architecture.md` §10 asks the service to avoid, just moved one process
earlier instead of removed.

### Move the secret to Redis or another shared cache, written by Frappe and read by the service
Not seriously considered. Adds a new shared-infrastructure dependency
(the service would need to know how to reach Redis, with its own auth) to solve
a problem a config file already solves with zero new infrastructure.

---

## Verification

- Unset every `FRAPPE_AI_*` environment variable; run plain `bench start`; the
  `ai` process starts successfully using `default_site` from
  `common_site_config.json` and the secret from that site's `site_config.json`.
- `frappe_ai/service/config.py`'s `load_settings()` raises a clear
  `ServiceConfigError` — not a bare `KeyError`/`FileNotFoundError` — when
  `frappe_ai_service_secret` is absent from `site_config.json`, or when the named
  site's directory doesn't exist.
- `GET /health` returns `frappe_reachable: true` when the service-side secret
  (read from disk) and the Frappe-side secret (read via `frappe.conf`) are the
  same file's same value — trivially true by construction, but worth confirming
  the merge logic (`common_site_config.json` + site override) produces the
  expected result when `frappe_ai_service_secret` is set in each independently.
- `AI Settings.service_secret` no longer exists as a field — confirmed via the
  DocType JSON and a fresh `bench migrate`.

---

## References

- [001 — Architecture §6.2, §10](../specifications/001-architecture.md)
- [ADR 0010 — Service bootstrap via environment variables](0010-service-bootstrap-via-env-vars.md) (superseded by this ADR)
- [ADR 0003 — Tools execute in Frappe](0003-tools-execute-in-frappe.md) — the acting-user
  identity this bootstrap auth is distinct from
- [ADR 0004 — SSE direct from FastAPI](0004-sse-direct-from-fastapi.md) — the run-token
  design that layers on top of this service-level auth
