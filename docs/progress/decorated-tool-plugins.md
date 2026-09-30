# Progress — Decorated FAC Plugins

> Tracks the `frappe_ai` side of decorated-tool-plugins.

---

## Overall Status

| | |
|---|---|
| **Status** | 🟡 Final validation |
| **Current phase** | Slice 11 — full gates |
| **Started** | 2026-09-23 |
| **Blockers** | None |

---

## Completed

- **2026-09-23** — Public API: `@plugin`, `@tool.read_only/write/read_write/privileged`.
- **2026-09-23** — `assistant_tools/registry.py` with `PluginSpec`/`ToolSpec` and generated `BaseTool` adapters.
- **2026-09-23** — `discovery.py` standalone scanner for `assistant_tools` packages.
- **2026-09-23** — `fac_compat.py` patches FAC discovery, loading, external tools, and migration sync.
- **2026-09-23** — `sync.py` reconciles decorated definitions directly into FAC plugin and tool configuration.
- **2026-09-23** — Removed the duplicate `AI Tool Plugin Registration` DocType; code owns definitions while FAC owns persisted policy.
- **2026-09-23** — Frontend bootstrap tolerates stale legacy `AI Tool` rows.
- **2026-09-23** — Unit tests: `tests/test_decorated_tools.py`, `tests/test_tool_discovery.py`.
- **2026-09-23** — All bench tests centralized under `frappe_ai/tests/` (including DocType tests in `tests/doctype/`).

## Remaining Work

- Commit on `feat/decorated-tool-plugins`.
- Full `frappe_ai` test suite gate.

## Change Log

| Date | Change |
|---|---|
| 2026-09-23 | Initial tracker created |
| 2026-09-23 | Removed duplicate plugin registration persistence in favor of FAC configuration |
