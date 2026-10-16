# Lesson 2 - How the stateless service talks back to Frappe

Files: `service/frappe_client.py` (caller), `api/service.py` (receiver).

## Why it exists
The service holds no database and no state. Everything it needs (agent config, message history, tool results, run persistence) is fetched from or pushed to Frappe over HTTP. `FrappeClient` is that single doorway.

## Prerequisites
- **Whitelisted method**: a Python function exposed at `/api/method/<dotted.path>`. Frappe wraps its return in `{"message": ...}`.
- **async / httpx**: the service is async so one process can wait on many slow calls; `httpx.AsyncClient` is the non-blocking HTTP client.

## Walkthrough
- Auth: every call sends `X-Frappe-AI-Service-Secret` and `X-Frappe-Site-Name`. A custom header is used because Frappe intercepts standard Bearer `Authorization` headers before whitelisted methods run.
- Methods, as constants: `get_service_config`, `get_run_config`, `get_fallback_model_config`, `dispatch_plugin_tool`, `persist_run_result`, `fail_run`, `log_diagnostic`.
- `_get_json` / `_post_json` build the URL, call httpx, convert `httpx.HTTPError` into `FrappeClientError`; `_unwrap` maps 401 to "rejected the service secret", any non-200 to an error with the first 500 chars of the body, and strips the `message` wrapper.
- Timeouts: 15 s for config/control calls; 300 s for `dispatch_plugin_tool` because tools may do OCR/PDF work.
- `log_diagnostic` is best-effort: failures are swallowed so logging never masks the real error.

## Receiving side (`api/service.py`)
- These endpoints use `allow_guest=True` (the service has no Frappe login) and call `_verify_service_secret()`: header present, configured secret present, `hmac.compare_digest`. That check is the access control.
- `get_run_config(run, user)`: verifies the secret, temporarily `frappe.set_user(user)` and sets `ignore_permissions` only to load the already-bound run/session/agent graph, always restoring in `finally`. It returns `agent` (instructions, max_iterations, temperature, top_p, reasoning, markdown, auto_approve), `model`, `tools`, `mcp_connections`, `messages` (prompt history), `config_snapshot`, and `questions` (pending confirmations for a resume).

## Flow
```
FastAPI stream_chat -> client.get_run_config(run, user)
  GET /api/method/frappe_ai.api.service.get_run_config?run=..&user=..
  Frappe: verify secret -> load graph as user -> build payload
FastAPI -> builder (Lesson 3) -> agent loop (Lesson 4)
  tool needed -> client.dispatch_plugin_tool(...) -> Frappe executes as user
  end -> client.persist_run_result(run, result)  (or fail_run)
```

## Production notes
- The shared secret is the only credential between processes; whoever has it can pass any `user`. The `user` is trusted because the secret holder is trusted; run-level checks (live run, owner) limit the blast radius.
- A new `httpx.AsyncClient` is created per call (no connection pooling): simple, slower under load.
- No retries on Frappe calls; failures surface as `FrappeClientError`.

## Checkpoint questions
1. Why a custom header instead of `Authorization: Bearer`?
2. `get_run_config` takes `user` as a parameter. Who guarantees it is the right user, and what bounds the damage if the service lies?
3. Why is `log_diagnostic` allowed to swallow errors but `persist_run_result` is not?
