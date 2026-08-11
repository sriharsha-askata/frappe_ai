# ADR 0010 — FastAPI service bootstraps its Frappe identity from environment variables

**Status:** Superseded by [ADR 0011](0011-service-secret-in-site-config.md)
**Date:** 2026-08-06
**Deciders:** Sri Harsha Dabbiru
**Prompted by:** Phase 2 implementation (FastAPI Service Skeleton)

---

## Context

Per `001-architecture.md` §10, the FastAPI service does not call `frappe.init`/
`frappe.connect` — all Frappe access is over HTTP, authenticated with the shared
secret stored in `AI Settings.service_secret` (§6.2).

This creates a bootstrap ordering problem. `AI Settings` is the source of truth for
service configuration (`request_timeout`, `stream_timeout`, `service_base_url`, and
the secret itself), and the natural instinct is "fetch config from Frappe at
startup." But the service's very first HTTP call to Frappe already needs to be
authenticated — Frappe has no reason to hand `service_secret` back to an
unauthenticated caller, and if it did, the secret would be worthless as an auth
mechanism. The service cannot fetch the credential it needs from the place that
credential is meant to protect.

Some value has to be supplied to the process independent of Frappe. The question is
only which value(s), and by what mechanism.

---

## Decision

**The FastAPI process receives its bootstrap identity — the shared secret and enough
site/network information to make its first HTTP call — via environment variables set
at process launch (Procfile / `bench start`, or a deployment's own env injection).**
It does not fetch these from Frappe, ever.

Four variables, all read once in `frappe_ai/service/config.py`:

| Variable | Purpose | Default |
|---|---|---|
| `FRAPPE_AI_SERVICE_SECRET` | Shared-secret credential for every Frappe-bound call | none — required |
| `FRAPPE_AI_SITE` | Which site's `AI Settings` to operate against (sent as `X-Frappe-Site-Name`) | none — required |
| `FRAPPE_AI_FRAPPE_URL` | Base URL of the Frappe web process | `http://127.0.0.1:8000` |
| `FRAPPE_AI_CORS_ORIGINS` | Browser origins allowed to call the service directly | `http://127.0.0.1:8000,http://localhost:8000` |

Everything else the service needs from `AI Settings` (`request_timeout`,
`stream_timeout`, `service_base_url`, `lancedb_path`, ...) is fetched lazily over
HTTP, authenticated with `FRAPPE_AI_SERVICE_SECRET` as a bearer token, via the
whitelisted `frappe_ai.api.service.get_service_config` endpoint (§ below). Frappe's
side of the secret lives in `AI Settings.service_secret` (a `Password` field,
`get_password`'d server-side) — the environment variable is expected to be **kept in
sync with it** by whoever provisions the deployment. Nothing in this design
synchronizes them automatically; that would just move the bootstrap problem rather
than solve it.

This is exactly one piece of state duplicated across two places (env var,
`AI Settings.service_secret`), which is the minimum irreducible requirement of a
design where two independently-deployable processes must mutually authenticate
without one trusting the other by default.

---

## Consequences

### Positive

- **No credential ever needs to travel from Frappe to the service over an
  unauthenticated channel.** The chicken-and-egg problem has no solution that avoids
  this except pre-shared state; env vars are the conventional way to hand a process
  pre-shared state at launch.
- **Matches how the Procfile already supervises the service.** `bench start` (via
  Honcho/Foreman-style Procfile semantics) can set per-line environment variables,
  so no additional file format or secrets-management layer is required for local
  dev.
- **Consistent with `AI Settings.service_secret` remaining the source of truth.**
  Frappe-side code (`mint_token`, the dispatch endpoint in Phase 3) reads the secret
  from the DocType, not from an env var — only the FastAPI process needs the
  environment-variable copy, because it is the one process that cannot read the
  DocType directly.
- **Multi-site ready without extra machinery.** `FRAPPE_AI_SITE` plus
  `X-Frappe-Site-Name` (confirmed supported in `frappe/app.py:get_site()`,
  independent of the `Host` header) is enough to target one specific site's
  `AI Settings` even when several sites share the bench, consistent with
  `001-architecture.md` §11.3 ("site count is small enough that one service
  instance can serve all sites; the site name is carried per request").

### Negative

- **Manual sync burden.** If `AI Settings.service_secret` is rotated in the desk,
  the environment variable must be updated and the service process restarted, or
  every service→Frappe call starts failing with 401. No rotation-without-restart
  mechanism exists yet. Acceptable for Phase 2 (development skeleton); worth
  revisiting if secret rotation becomes an operational requirement before Phase 8.
- **A misconfigured env var fails closed but not loudly until first use.**
  `config.py` validates presence at import time (raises before the app starts
  serving), which converts "silently broken" into "won't start" — a deliberate
  trade-off in this design, not an oversight.
- **One more piece of deployment surface** beyond the DocType-driven configuration
  this app otherwise prefers (`001-architecture.md` §4: "Configuration ... UI-editable,
  versioned, restart-free"). This is the one config value that structurally cannot
  be UI-editable-and-restart-free at the same time, because it is also the
  authentication mechanism for fetching UI-editable config.

### Neutral

- `FRAPPE_AI_CORS_ORIGINS` is bootstrap-supplied rather than fetched from
  `AI Settings`, for the same reason as the secret: CORS is enforced by Starlette's
  middleware at app-construction time, before any request (and therefore before any
  Frappe round-trip) can happen. A future phase could make it hot-reloadable by
  moving CORS enforcement into a request-time check instead of static middleware,
  if that proves necessary — out of scope here.

---

## Alternatives Considered

### Fetch config from Frappe first, using `allow_guest=True` on the config endpoint
Rejected outright. An unauthenticated endpoint that returns `service_secret` (even
indirectly) to any caller defeats the purpose of having a secret. This was never a
serious option, but stating it makes the chicken-and-egg problem's actual shape
explicit: *some* pre-shared value is unavoidable.

### A local secrets file (`.env` parsed by the service, not exported as real env vars)
Considered as the literal file format. Not rejected — `config.py`'s
`os.environ.get(...)` reads work identically whether the values arrive via a real
exported environment or a `.env` loaded by the process manager before exec. The
Procfile entry documented in this phase uses plain environment variables (simplest,
matches other Procfile lines like `redis_cache`/`redis_queue` which take no app-level
secrets at all); a `.env` loader can be layered in later without changing
`config.py`'s contract if a deployment prefers that convention.

### Service reads `site_config.json` directly off disk
Rejected. This would reintroduce a filesystem coupling to a specific site's bench
layout that `001-architecture.md` §10 explicitly avoids ("independently deployable
and horizontally scalable"). It would also still not solve the actual problem:
`site_config.json` does not contain `AI Settings.service_secret` (that lives in the
database, encrypted), so this alternative does not even address the bootstrap
question — it was only ever a way to learn the site name, which `FRAPPE_AI_SITE`
already supplies more portably.

### Auto-generate and register the secret on first service startup (service calls an `allow_guest` "register me" endpoint once)
Rejected as a self-inflicted TOCTOU/replay problem: the registration endpoint itself
would need to be guarded by *something*, and whatever guards it is the real
bootstrap credential — this alternative just renames the problem rather than solving
it, while adding a stateful first-run code path that has to be gotten right exactly
once per deployment and is hard to test.

---

## Verification

- `frappe_ai/service/config.py` raises a clear error at import time if
  `FRAPPE_AI_SERVICE_SECRET` or `FRAPPE_AI_SITE` is unset — the service refuses to
  start rather than start half-configured.
- `GET /health` succeeds end-to-end (service → Frappe `get_service_config` → 200)
  when the Procfile-supplied env var matches `AI Settings.service_secret`; a
  deliberately mismatched value in either place produces `frappe_reachable: false`
  in the health payload rather than a crash.
- `AI Settings.service_secret` is never returned by any FastAPI response — the
  service holds it only long enough to set the `X-Frappe-AI-Service-Secret` header
  per request. That header is deliberately not the standard `Authorization` header:
  Frappe core's `validate_auth()` intercepts any `Authorization: Bearer ...` value
  itself (as an OAuth bearer token attempt) and raises `AuthenticationError` before
  a whitelisted method's body runs if that validation fails — discovered during
  Phase 2's own end-to-end verification, and worth recording here since it directly
  shapes how this bootstrap secret is carried on the wire.

---

## References

- [001 — Architecture §6.2, §10, §11.3](../specifications/001-architecture.md)
- [ADR 0003 — Tools execute in Frappe](0003-tools-execute-in-frappe.md) — the acting-user
  identity this bootstrap auth is distinct from
- [ADR 0004 — SSE direct from FastAPI](0004-sse-direct-from-fastapi.md) — the run-token
  design that layers on top of this service-level auth
