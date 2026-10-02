# ADR 0011 — The shared secret lives in `site_config.json`

**Status:** Accepted · **Date:** 2026-08-06

## The problem

The FastAPI service must prove itself to Frappe on its very first call, so it needs the shared secret *before* it can ask Frappe for anything. The first design stored the secret twice: a Password field on `AI Settings` for Frappe, and an environment variable `FRAPPE_AI_SERVICE_SECRET` for the service, kept in step by hand.

That failed in practice. Plain `bench start` did not start the `ai` process because nobody had exported the variable; only `ai` crash-looped, quietly. Worse, if someone rotated the database value and forgot the variable, the service would run with a stale secret. A secret held in two places with nothing enforcing that they match fails silently by default.

## The decision

**One place: `frappe_ai_service_secret` in `sites/<site>/site_config.json`**, the file where Frappe already keeps per-site secrets such as `db_password` and `encryption_key`. The `AI Settings.service_secret` field was removed.

- **Frappe side:** reads `frappe.conf.frappe_ai_service_secret` (`api/service.py`, `api/dispatch.py`, `api/api.py`, `triggers/triggers.py`).
- **Service side:** `service/config.py` reads `site_config.json` straight off disk as plain JSON, merged over `common_site_config.json` the way Frappe does. It does **not** start Frappe or open a database connection.
- **Site name:** `FRAPPE_AI_SITE`, or the bench's `default_site` when unset. With a `default_site`, `bench start` boots the service with **no** required environment variables.

Other settings (timeouts, URLs) stay on `AI Settings` and are fetched over HTTP. The secret travels in the header `X-Frappe-AI-Service-Secret`, never in `Authorization`, which Frappe core intercepts.

## What follows

**Good**
- `bench start` boots the service unattended.
- One source of truth, nothing to keep in sync.
- It matches how other Frappe deployment secrets are already stored.
- No database lookup or decryption on each service call.

**Costs**
- The secret is plain text on disk, not encrypted like a Password field. That is the same as `db_password` in the same file, but it is weaker than the old database field.
- Rotating it means editing a file on the Frappe host (restart the service afterwards), not saving a form.
- The service now depends on the bench's folder layout (`_bench_root()` finds `sites/` from its own install path). To run it on another machine, set `FRAPPE_AI_SITES_PATH`.
- One service process reads one site's secret (see [001](../specifications/001-architecture.md) §9).

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Keep two copies and document the variable better | It was already documented. The flaw is a manual, unchecked step |
| A `.env` file at the bench root | Still a second place to configure, and no per-site scoping |
| A launcher script that reads `AI Settings` before starting `uvicorn` | Needs a working Frappe context just to read one value |
| Redis or another shared store | A new dependency to solve what a config file already solves |

## How we check

- With no `FRAPPE_AI_*` variables set, `bench start` starts the service.
- `load_settings()` raises a clear `ServiceConfigError` if the secret is missing or the site folder does not exist.
- `GET /health` reports `frappe_reachable: true` when both sides read the same value.
- `AI Settings.service_secret` no longer exists.

Tests: `frappe_ai/tests/test_service_auth.py`, `test_service_app.py`.

## Related

[Setup](../setup.md), [001 §5](../specifications/001-architecture.md), [ADR 0004](0004-sse-direct-from-fastapi.md).
