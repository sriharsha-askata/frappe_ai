# ADR 0005 — Greenfield build; no data migration from `flow`

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Context

`apps/flow` and `apps/frappe_ai` are both present in this bench and both listed in
`sites/apps.txt`. `frappe_ai` is an untouched `bench new-app` skeleton — one commit
("feat: Initialize App"), no DocTypes, no logic.

`flow` defines 16 DocTypes whose `frappe_ai` counterparts differ only by the `Flow ` →
`AI ` name prefix and a handful of added fields. A migration path is therefore
*technically* available: rename DocTypes, repoint Link fields, copy rows.

`flow` itself contains precedent — `flow/patches/rename_ai_to_flow.py` renamed 15 `AI *`
DocTypes to `Flow *`, including raw SQL to repoint the `__Auth` table, because Password
field values are keyed by DocType name and are **not** migrated by `rename_doc`.

The question is whether `frappe_ai` should ship equivalent migration patches.

---

## Decision

**No migration patches. No coexistence guarantees.**

`frappe_ai` is built greenfield, treating `flow` purely as a functional specification.
Once `frappe_ai` reaches parity, `flow` is uninstalled and its data discarded.

Explicitly out of scope:

- Patches copying `Flow *` rows into `AI *` DocTypes
- `__Auth` repointing for Password fields
- Vector store migration
- Any runtime interoperability between the two apps
- Any period during which both apps are expected to work on the same data

---

## Consequences

### Positive

- **Substantially less work.** Migration patches for 16 DocTypes with Link repointing,
  Password migration, child-table reparenting, and vector reindexing would be a phase in
  their own right — and among the hardest to test.
- **No compatibility tax on the design.** Free to change field semantics, rename fields,
  merge `Flow Knowledge Settings` into a broader `AI Settings`, and add Agno-specific
  fields without preserving any legacy shape.
- **No dual-write or dual-read complexity.** No period where both apps must agree about
  the same rows.
- **The `__Auth` hazard is avoided entirely.** Password field migration is genuinely
  error-prone; `flow`'s own patch needed raw SQL to get it right.
- **Clean git history.** No inherited rename patches in a brand-new app.

### Negative

- **Existing `flow` data is lost.** Agents, tools, sessions, runs, knowledge bases, and
  memories do not carry over. Agents and knowledge bases must be recreated by hand.
- **Historical audit trail is lost.** `Flow Run` records disappear with the app. If any
  compliance obligation attaches to them, they must be exported before uninstall.
- **Knowledge bases must be re-ingested.** Sources are re-created and re-embedded, costing
  embedding API calls proportional to corpus size.
- **No rollback to `flow` after uninstall.** Once removed, reverting means reinstalling and
  reconfiguring from scratch.

### Neutral

- Both apps can remain installed during development — the DocType names do not collide.
  This is a development convenience, not a supported production configuration.

---

## Alternatives Considered

### Parity, then migrate, then retire
Build `frappe_ai`, ship patches copying every `Flow *` row into its `AI *` counterpart,
then uninstall `flow`.
**Rejected by the user.** It would preserve history and configuration, but the cost —
Password/`__Auth` handling, Link repointing across 16 DocTypes, vector reindexing, and a
substantial test matrix — is not justified when the current `flow` data is not production
data worth keeping.

### Permanent coexistence
Keep both installed; `flow` for existing agents, `frappe_ai` for new ones.
**Rejected.** Two AI frameworks on one site means duplicated concepts (two agent DocTypes,
two tool registries, two chat panels), duplicated `doc_events` wildcard hooks firing on
every document write, and permanent user confusion about which app owns what.

### Rename `flow` in place
Rename the app and its DocTypes rather than writing a new one.
**Rejected.** The runtime is being replaced wholesale (Agno + FastAPI, per
[ADR 0001](0001-agno-fastapi-over-frappe-native.md)); very little of `flow`'s orchestration
code survives. A rename would carry the git history of code that is being deleted, and
`frappe_ai` already exists as the intended target.

---

## Pre-Uninstall Checklist

Before `bench uninstall-app flow`, confirm with the site owner:

- [ ] No production agents are in active use in `flow`
- [ ] `Flow Run` history is not needed for audit/compliance (export if it is)
- [ ] All knowledge sources are recorded so they can be recreated in `frappe_ai`
- [ ] Custom `Flow Tool` scripts have been copied into `AI Tool` rows
- [ ] Any `Flow Trigger` automations have `AI Trigger` equivalents and have been observed firing
- [ ] Provider API keys are available for re-entry (Password fields cannot be read back)
- [ ] A database backup exists

The last item matters most: uninstalling drops the tables.

---

## Verification

- `frappe_ai` installs on a site with no `flow` data present.
- Every feature in [002 — Feature Mapping](../specifications/002-feature-mapping.md) is
  demonstrable in `frappe_ai` without reference to `flow`.
- `bench uninstall-app flow` leaves `frappe_ai` fully functional.

---

## References

- [002 — Feature Mapping](../specifications/002-feature-mapping.md)
- [Progress tracker](../progress/flow-to-frappe-ai-migration.md)
- `apps/flow/flow/patches/rename_ai_to_flow.py` — the precedent not being followed
