# Lesson 1 - Entry, configuration and the run token

Files: `service/config.py`, `service/auth.py`, `service/main.py`, `start_run` and `_mint_stream_response` in `api/api.py`.

## Concepts
- **SSE**: the server keeps one HTTP response open and sends many `data: {...}` frames, so text can appear as the model produces it.
- **HMAC token**: a signature over the token fields using a secret only Frappe and FastAPI know. FastAPI recomputes it locally; a match proves Frappe issued it and nothing changed.

## Flow
1. `start_run` (api/api.py:31) validates input, resolves/creates the `AI Session`, loads the `AI Agent`, creates an `AI Run` with `config_snapshot` (a frozen copy of agent settings), persists the user turn, then calls `_mint_stream_response`.
2. `_mint_stream_response` reads `frappe_ai_service_secret` from site config and returns `{run, session, token, stream_url, expires_in}`. No AI work yet.
3. `mint_run_token` signs `run|session|user|expiry` (joined with `\x1f`) with HMAC-SHA256, TTL 300 s, base64url-encoded.
4. Browser POSTs to `stream_url` with `Authorization: Bearer <token>`.
5. `main.py::stream_run` rejects a missing header (401), runs `verify_run_token` (malformed / signature mismatch via `hmac.compare_digest` / expired), checks `payload.run == run`, then returns a `StreamingResponse` (`text/event-stream`, `X-Accel-Buffering: no`) from `stream_chat`.
6. `config.py::load_settings` reads `site_config.json` straight from disk (the service never calls `frappe.init`); a missing secret raises `ServiceConfigError` at import, so the service refuses to boot.

## Production notes
- Handled: forgery (HMAC, constant-time compare), cross-run replay (run id check), fail-closed boot.
- Partial: the 300 s TTL covers stream setup only; the token is a bearer credential (TLS is a deployment matter); one shared secret, no rotation; CORS methods/headers are `*`.
- The token alone never authorises actions: Frappe re-validates the run on each tool call (dispatch requires a live run owned by the user).

## Checkpoint questions
1. Why verify locally instead of asking Frappe each time, and what is given up?
2. What attack appears if `payload.run != run` is removed?
3. Why snapshot config into `AI Run` instead of reading the live `AI Agent`?
4. Why doesn't the 300 s TTL limit how long a chat streams?
