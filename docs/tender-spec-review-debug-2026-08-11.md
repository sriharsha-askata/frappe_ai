# Tender Spec Review Debug Notes

Date: 2026-08-11
Site: `tact.local`
Enquiry: `E-2026-0005`

## Summary

Tender spec review runs for `E-2026-0005` have failed through multiple distinct issues while migrating tender orchestration onto `frappe_ai` manual triggers.

Several bugs were identified and patched in code, but multiple runs were created before each fix was live. Because of that, historical run states are mixed:

- some runs are stuck in `Running`
- some runs are correctly marked `Failed`
- some runs appear to have disappeared or were not readable afterward

## Verified Failure Chain

### 1. `get_run_config` executed as `Guest`

Observed error:

- `User Guest does not have doctype access via role permission for document AI Run`

Impact:

- FastAPI service could not fetch run config
- tender stage log moved to `started`
- run never actually began

Fix applied:

- `frappe_ai/api/service.py`
- `get_run_config()` now switches to the explicit acting user before loading `AI Run` and `AI Session`

Verification:

- direct `curl` to `http://127.0.0.1:8000/api/method/frappe_ai.api.service.get_run_config?...`
- returned valid JSON `message` payload for a real run after restart

### 2. `fail_run` and `persist_run_result` also executed as `Guest`

Observed error:

- service attempted cleanup callback
- `POST /api/method/frappe_ai.api.api.fail_run` returned `403`

Impact:

- broken runs remained stuck as `Running`
- no terminal state persisted

Fix applied:

- `frappe_ai/api/api.py`
- `fail_run()` and `persist_run_result()` now temporarily execute as `Administrator` after validating the shared secret

Verification:

- `frappe_ai.tests.test_api` updated and passing

### 3. Provider rejected `developer` message role

Observed error:

- `Error from provider (Console): ... messages[0].role: unknown variant 'developer'`

Impact:

- run reached model execution
- provider refused request before tool execution

Fix applied:

- `frappe_ai/service/builder.py`
- `frappe_ai/service/routes/chat.py`
- stopped relying on Agno instructions path that produced provider-specific `developer` role
- preserved transcript `system` message instead

Verification:

- `frappe_ai.tests.test_service_app` updated and passing

### 4. SSE stream could end without terminal event and leave run stuck

Observed behavior:

- worker job completed quickly
- `AI Run` remained `Running`
- `iterations = 0`
- no `error`

Interpretation:

- `_run_via_service()` could return after stream closure without seeing `done` or `error`
- if the run row had not already reached a terminal state, the worker still treated the call as successful

Fix applied:

- `frappe_ai/triggers/triggers.py`
- `_run_via_service()` now raises if the stream ends without a terminal SSE event and the run is still non-terminal

Intended result:

- future runs should fail explicitly instead of hanging forever

## Runs Observed During Debugging

Historical runs seen for `E-2026-0005`:

- `mjul94v1rk`
- `pkunpe5agg`
- `vrbnqjp4h8`
- `4kbf3qvrbk`
- `8ak7lvtapa`
- `c9tnd3pj18`
- `fq6i9o2ino`

Known statuses during debugging:

- `4kbf3qvrbk` failed with provider `developer` role error
- `8ak7lvtapa` failed with provider `developer` role error
- `c9tnd3pj18` still showed old `403` callback path before latest restart
- `fq6i9o2ino` was linked to `Stage Log 18`, worker completed quickly, and later `AI Run fq6i9o2ino not found` was observed

## Stage Log Mapping Observed

Relevant stage logs observed during this investigation:

- `13`
- `14`
- `15`
- `16`
- `17`
- `18`

Confirmed mapping:

- run `fq6i9o2ino` corresponded to `stage_log_id = 18`

## Current State

As of the end of this debugging session:

- direct `get_run_config` curl against `web.1` succeeds
- callback auth bugs were patched
- provider `developer`-role bug was patched
- missing-terminal-SSE handling was patched

But a full clean successful tender spec review run has not yet been confirmed end-to-end after all patches were live together.

## Most Likely Remaining Unknowns

If failures continue after all current fixes are live, the next likely areas are:

- MCP toolkit initialization
- MCP stdio server startup/runtime failure
- tool execution failure in tender-specific tools
- run lifecycle inconsistency after worker/service disconnect

`worker.log` did contain:

- `Failed to initialize MCP toolkit`

That message was not yet fully traced to a specific failed tender run in this session.

## Recommended Next Checks

After the next fresh spec-review attempt:

1. Check newest `AI Run` for `E-2026-0005`
2. Check newest `Stage Log` for `E-2026-0005`
3. Inspect `ai.1` console output for the exact new run id
4. Inspect `logs/worker.log` for:
   - `execute_spec_review_workflow`
   - `stage_log_id`
   - `Failed to initialize MCP toolkit`
5. If run remains non-terminal, probe `POST /stream/<run>` directly with a minted token

## Files Changed During Debugging

- `frappe_ai/api/service.py`
- `frappe_ai/api/api.py`
- `frappe_ai/service/builder.py`
- `frappe_ai/service/routes/chat.py`
- `frappe_ai/triggers/triggers.py`
- `frappe_ai/tests/test_api.py`
- `frappe_ai/tests/test_service_api.py`
- `frappe_ai/tests/test_service_app.py`
- tender migration-related files updated earlier in the session

