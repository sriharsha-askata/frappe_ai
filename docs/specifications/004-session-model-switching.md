# 004 — Mid-Session Model Switching

**Status:** Planned, not implemented.
**Applies to:** `AI Session`, `frappe_ai.api.api.start_run`.

---

## 1. Problem

`AI Session.model` is meant to let a session override its agent's default model.
[003-doctype-reference.md](003-doctype-reference.md) §8 already documents this as
existing behaviour — *"model may be overridden"* (only `agent` is documented as locked)
— and `start_run`'s own docstring in
[api.py](../../frappe_ai/api/api.py) already claims *"model may still override the
session's model for this turn."*

Neither is actually implemented. `_resolve_session` (`api.py:325-345`) loads an existing
session as-is and silently discards the `model` argument — the override only ever applies
at session **creation**. Once a session exists, there is no way to change which `AI Model`
it uses; a new session (losing transcript/history) is the only workaround today.

This is a gap between documented and implemented behaviour, not a new design decision —
closing it does not need an ADR.

## 2. Current behaviour (verified against the code)

- `_resolve_session(session, *, agent, model)` — for an existing `session`, returns the
  loaded doc unchanged; `model` is never assigned or saved. Only the new-session branch
  writes `model`.
- `AI Session.model` is a plain, non-`read_only` `Link` field. `validate()`
  (`ai_session.py:72-74`) only runs `_validate_agent_unchanged()` (locks `agent`) and
  `_validate_model_enabled()` (rejects a disabled model, no-op if empty) — there is no
  immutability guard on `model` at the doctype layer. A direct `doc.model = "Other";
  doc.save()` already works today; `test_session.py::test_model_override_allowed`
  (line 72) already proves this — the doctype was never the blocker.
- `_check_agent_usable(agent_doc, session_doc.model)` (`api.py:348-363`) validates the
  effective model (permitted + enabled) on every `start_run` call, regardless of when it
  was set — this already works correctly and needs no change.
- `AI Run.config_snapshot` is built fresh on every `start_run` via
  `agent_doc._snapshot(model=session_doc.model)` (`api.py:91`, `ai_agent.py:94-106`), and
  the FastAPI service resolves the model **live** from `session_doc.model or
  agent_doc.model` at stream time (`api/service.py`'s `get_run_config`), not from the
  frozen `config_snapshot`. So once `AI Session.model` is switchable, the very next
  `start_run` will naturally produce a run on the new model with no service-side change.
- **Resume hazard**: `resume_run` never re-snapshots; it re-mints a token and the service
  re-fetches config live. If a model switch were allowed while a run is `Paused`, resuming
  that run would silently pick up the *new* model even though the paused run's own
  `config_snapshot` and prior tool-call state were generated under the *old* model.
  `AISession.assert_not_blocked()` (`ai_session.py:282-311`) already refuses new turns
  while a run is `Paused` or genuinely `Running` — reusing it as a guard on the switch
  itself closes this gap without touching `resume_run`.

## 3. Planned implementation

**`frappe_ai/api/api.py` — `_resolve_session` (lines 325-345)**

Add a model-diff branch to the existing-session path, guarded by `assert_not_blocked()`:

```python
def _resolve_session(session, *, agent, model):
	if session:
		doc = frappe.get_doc("AI Session", session)
		assert_session_owner(doc)
		if model and model != doc.model:
			doc.assert_not_blocked()  # refuse to switch mid-flight; also protects resume
			doc.model = model
			doc.save(ignore_permissions=True)
		return doc
	...
```

`save()` runs `validate()` → `_validate_model_enabled()`, so a disabled model is rejected
here too, on top of `_check_agent_usable`'s later check. `start_run`'s docstring should
drop its stale "only if the session hasn't set one yet" caveat once this lands.

No changes needed to `_check_agent_usable`, `AIAgent._snapshot`, `create_run`,
`AgentBuilder`, `resume_run`, or the service layer — all already resolve the effective
model correctly once `AI Session.model` itself is switchable, and the in-flight guard
makes it unreachable for a paused run to resume under a different model than it paused
under.

## 4. Tests to add

In `frappe_ai/tests/test_api.py`, in or near `TestStartRunValidation` (line 157):

- `test_start_run_switches_session_model` — a second `start_run(session=..., model=<other
  enabled AI Model>)` updates `AI Session.model` and the new run's `config_snapshot`.
- `test_start_run_model_switch_rejects_disabled_model` — switching to a disabled model
  raises `frappe.ValidationError` (reuse the fixture pattern from `test_session.py`'s
  `TestAISessionModelEnabledValidation`, line 79).
- `test_start_run_model_switch_blocked_while_run_in_progress` — a second `start_run` with
  a different `model` while the session already has a `Running`/`Paused` run is rejected
  with the existing `assert_not_blocked` message.
- `test_start_run_model_switch_requires_permission` — switching to a model the acting user
  can't read is rejected.

Run: `bench --site tact.local run-tests --app frappe_ai --module frappe_ai.tests.test_api`,
then the full suite once to confirm no regression against the baseline pass count.

## 5. Documentation to update once implemented

- [003-doctype-reference.md](003-doctype-reference.md) §8 — extend the `AI Session`
  Controller bullet to name the mechanism and guard: *"Model may be changed on an existing
  session via `start_run(session=..., model=...)`, blocked by `assert_not_blocked()` while
  a run is `Paused`/`Running`."*
- [001-architecture.md](001-architecture.md) §5.1 — one clause on the "resolve/create AI
  Session" step noting the model may switch there if provided and no run is in flight. No
  diagram restructuring.
- [progress/flow-to-frappe-ai-migration.md](../progress/flow-to-frappe-ai-migration.md) —
  one Change Log line once done; this is Phase-3 scope (a follow-up fix), not a new phase.

## 6. Out of scope

- Per-message model attribution — `AI Session Message` has no `model` field; each
  `AI Run.config_snapshot` already disambiguates per turn, and there's no transcript UI
  yet to need it (Phase 6 frontend hasn't started). Revisit if/when that UI is built.
- Any change to `resume_run` itself — see §3.
