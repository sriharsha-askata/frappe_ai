# `service_health()` crashes with `NameError` when unconfigured

**Type:** Bug
**Severity:** Critical
**Status:** Open — approved, not yet implemented
**Source:** Architecture review, 2026-09-06 (Finding C-1 / Decision 1)

---

## Description

`service_health()` (`frappe_ai/api/service.py:110`) is a whitelisted Frappe method the
desk UI calls to check whether the FastAPI service is reachable. When
`AI Settings.service_base_url` is unset, it is supposed to return a friendly
`{"success": False, "message": "AI Settings.service_base_url is not configured."}`
response.

Instead, inside the `if not base_url:` branch, it executes:

```python
plugin_tools = _resolve_agent_plugin_tools(agent_doc, user)
```

Neither `agent_doc` nor `user` is defined anywhere in this function. This raises
`NameError` immediately, before the intended message is ever returned. The line's
value is never used — `return` follows two lines later regardless of what
`plugin_tools` holds. This is a real, reproducible crash, confirmed by direct code
read, not a hypothetical.

## Why it needs to be done

A fresh `frappe_ai` install starts with `service_base_url` empty by default, so
**every new site hits this exact branch the first time anyone checks service health
from the desk**, before the FastAPI service has even been configured. The crash
replaces a helpful "not configured" message with an opaque traceback at the worst
possible moment: first contact with the app.

No existing test covers this branch (`service_health()`'s unconfigured-`base_url`
path has no test file exercising it), which is why the bug shipped unnoticed.

## Fix

Delete the dead line. It is behavior-preserving for every other path through this
function — nothing downstream references `plugin_tools`.

```python
if not base_url:
    return {
        "success": False,
        "message": _("AI Settings.service_base_url is not configured."),
        "data": {},
    }
```

## Trade-offs

None identified. This is as close to a free fix as exists in the codebase.

## Migration impact

None. No schema, no API contract, no caller change — the function's return shape
is unaffected for every path except the one that currently crashes.

## Verification

Call `service_health()` (or trigger it from the desk UI) with `service_base_url`
unset. Confirm it returns the "not configured" message instead of raising.

---

## Resolution

Resolved 2026-09-06 by the `service_health()` NameError + `AI Settings.service_base_url`
removal plan. The dead `plugin_tools = _resolve_agent_plugin_tools(agent_doc, user)` line
was deleted; the `service_base_url` field was removed from the `AI Settings` DocType
and replaced with a `frappe_ai_service_url` key in `sites/<site>/site_config.json`,
read by `frappe_ai/api/_service_url.get_service_url()`. Regression test:
`frappe_ai/tests/test_service_api.py::TestServiceHealth::test_service_health_no_url_set_returns_friendly`.
