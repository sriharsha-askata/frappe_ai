# ADR 0004 — The browser streams directly from the FastAPI service

**Status:** Accepted · **Date:** 2026-08-05

## The problem

The answer must reach the browser token by token. The older app streamed Server-Sent Events (SSE) through Frappe itself, which kept a Frappe worker busy for the whole run and needed awkward transaction handling (Frappe's end-of-request commit had already run by the time the stream finished). With the agent loop moving to FastAPI ([ADR 0001](0001-agno-fastapi-over-frappe-native.md)), the streaming path has to be chosen again.

## The decision

**The browser opens the stream straight to the FastAPI service (port 8001)**, using a short-lived token that Frappe issues for that one run.

1. The browser calls `start_run` on Frappe.
2. Frappe creates the `AI Run`, saves the user's message, and returns `{run, session, token, stream_url, expires_in}`.
3. The browser sends `POST stream_url` with `Authorization: Bearer <token>`. It uses `fetch` and reads the body as a stream, which allows a JSON body (needed to resume a paused run).
4. The service verifies the token, builds the agent and streams events.
5. At the end, the service posts the result back to Frappe to be saved.

**Token:** an HMAC over `(run, session, user, expiry)` with the shared secret from `site_config.json` ([ADR 0011](0011-service-secret-in-site-config.md)); tied to one run; valid for 300 seconds, which covers opening the stream, not its length; verified by the service itself.

**Events** (`text/event-stream`; headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`): `run_started`, `text`, `tool_started`, `tool_ended`, `error`, `done`. Payloads are in [005](../specifications/005-frontend-contract.md).

### Heartbeats: planned, not built

A reverse proxy or load balancer between the browser and port 8001 will usually close a connection that is silent for 30–60 seconds, and a slow tool call or long reasoning step can be silent that long. The plan is for the service to send a small keep-alive event (`ping`) about every 15 seconds. **This is not implemented yet.** Until it is, streaming works on localhost and may fail behind proxies with short idle timeouts; raise the proxy's idle timeout for the service route. See [010](../specifications/010-review-topics.md).

## What follows

**Good**
- Frappe workers are never held during a run. Proxying would have lost this.
- Lowest latency: no extra hop.
- No commit-on-`Done` choreography: the service streams natively and saves through an explicit callback.
- The frontend change was small: a different origin and a `Bearer` token.
- Client disconnects are visible to the service, so an abandoned run can be marked Failed.

**Costs**
- Two origins: the service needs CORS configured for the site's origin (`FRAPPE_AI_CORS_ORIGINS`). Behind a reverse proxy, map a path to port 8001 to keep one public origin.
- New security-critical code: token creation and checking.
- Port 8001 must be reachable by the browser but should not be exposed publicly except through a proxy.
- Stuck runs still need recovery on the Frappe side: `recover_session`, `stop_run`, and failing a run that stays *Running* beyond 300 seconds.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Proxy the stream through Frappe | One origin and no token, but it holds a Frappe worker for every run, which is the limit we are removing |
| Frappe realtime (socket.io via Redis) | An extra hop and Redis on the hot path, and a bigger frontend rewrite. May be worth revisiting if reconnection becomes important |
| Poll `AI Run` | More load than the design it replaces, and not token-by-token |

**Deferred:** resuming a broken stream with `Last-Event-ID` would need every event stored per run. Today, if the stream breaks, the run still finishes or fails on the server and the result can be read back from `AI Run`.

## How we check

- Text appears incrementally.
- During a long run the Desk stays responsive.
- A token for run A cannot stream run B; an expired token is rejected.
- Closing the tab mid-run marks the run Failed.

## Related

[001 §4, §5, §7](../specifications/001-architecture.md), [005](../specifications/005-frontend-contract.md).
