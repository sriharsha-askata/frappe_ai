# 004 — Switching the model in a session

A session is locked to one **agent**, but you can change which **model** it uses between turns. For example, start on a fast model and switch to a stronger one for a hard question. The conversation is kept.

## How it works

The browser calls `start_run` with `session` and a different `model`. In `frappe_ai/api/api.py`, `_resolve_session` does this for an existing session:

1. Loads the session and checks that the caller owns it (`assert_session_owner`).
2. If `model` differs from the session's current model, calls `AISession.assert_not_blocked()` and then saves the new model on the session.
3. `save()` runs `validate()`, which rejects a disabled model. `start_run` then runs `_check_agent_usable`, which rejects a model the user may not read.

From then on every new run resolves its model as `session.model or agent.model`. The service looks this up live in `get_run_config`, so no service change is involved.

## Why a run in progress blocks the switch

`assert_not_blocked()` refuses while a run of the session is **Paused** or **Running**. This matters for resume: `resume_run` does not take a new snapshot; the service fetches the config again when the stream opens. If the model could change while a run was Paused, the resumed run would continue on the *new* model although its earlier steps came from the old one. Blocking the switch while a run is open avoids that.

(`assert_not_blocked` also fails a *Running* run older than 300 seconds, treating it as abandoned, so a crashed stream cannot block a session forever.)

## What is not tracked

Messages do not record which model wrote them. Each `AI Run.config_snapshot` records the model for its turn, which is enough to tell later. If the transcript UI ever needs to show a model per message, add a field then.

## Where the tests are

`frappe_ai/tests/test_api.py`:

- `test_start_run_switches_session_model`
- `test_start_run_model_switch_rejects_disabled_model`
- `test_start_run_model_switch_blocked_while_run_in_progress`
- `test_start_run_model_switch_requires_permission`
