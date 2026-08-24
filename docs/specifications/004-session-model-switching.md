# 004 — Mid-Session Model Switching

**Status:** Implemented and verified 2026-08-07.
**Applies to:** `AI Session`, `frappe_ai.api.api.start_run`.

---

## 1. Problem and resolution

`AI Session.model` is meant to let a session override its agent's default model.
[003-doctype-reference.md](003-doctype-reference.md) §8 already documents this as
existing behaviour — *"model may be overridden"* (only `agent` is documented as locked)
— and `start_run`'s own docstring in
[api.py](../../frappe_ai/api/api.py) already claims *"model may still override the
session's model for this turn."*

This was originally a documentation/implementation gap. `_resolve_session` now applies a
provided model to an existing session when it differs from the current value, after
checking that the session has no active or paused run. The session keeps its transcript
while subsequent runs use the selected model.

This is a gap between documented and implemented behaviour, not a new design decision —
closing it does not need an ADR.

## 2. Implemented behaviour

- `_resolve_session(session, *, agent, model)` updates and saves `AI Session.model` for
  an existing session when a different model is supplied.
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

## 3. Implementation

**`frappe_ai/api/api.py` — `_resolve_session` (lines 325-345)**

The existing-session path uses a model-diff branch guarded by `assert_not_blocked()`:

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

## 4. Verification

In `frappe_ai/tests/test_api.py`, in or near `TestStartRunValidation` (line 157):

- `test_start_run_switches_session_model` verifies the session and new run snapshot.
- `test_start_run_model_switch_rejects_disabled_model` verifies disabled-model rejection.
- `test_start_run_model_switch_blocked_while_run_in_progress` verifies the active-run guard.
- `test_start_run_model_switch_requires_permission` verifies model read permissions.

These tests live in `frappe_ai/tests/test_api.py` and passed with the implementation.

Run: `bench --site tact.local run-tests --app frappe_ai --module frappe_ai.tests.test_api`,
then the full suite once to confirm no regression against the baseline pass count.

## 5. Documentation alignment

- [003-doctype-reference.md](003-doctype-reference.md) §8 — extend the `AI Session`
  Controller bullet to name the mechanism and guard: *"Model may be changed on an existing
  session via `start_run(session=..., model=...)`, blocked by `assert_not_blocked()` while
  a run is `Paused`/`Running`."*
- [001-architecture.md](001-architecture.md) §5.1 — one clause on the "resolve/create AI
  Session" step noting the model may switch there if provided and no run is in flight. No
  diagram restructuring.
- [progress/flow-to-frappe-ai-migration.md](../progress/flow-to-frappe-ai-migration.md) —
  the implementation is recorded in the change log; this remains Phase-3 scope, not a
  new phase.

## 6. Out of scope

- Per-message model attribution — `AI Session Message` has no `model` field; each
  `AI Run.config_snapshot` already disambiguates per turn, and there's no transcript UI
  yet to need it for per-message model attribution. Revisit if the transcript UI needs
  to display the model used for each turn.
- Any change to `resume_run` itself — see §3.
