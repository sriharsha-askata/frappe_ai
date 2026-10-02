# ADR 0005 — A new app; no data migration from the older `flow` app

**Status:** Accepted · **Date:** 2026-08-05

## The problem

An earlier app called `flow` already did AI agents inside Frappe. `frappe_ai` has almost the same DocTypes (with `AI` in place of `Flow` in their names), so copying the data across was technically possible: rename the DocTypes, repoint links, copy rows. Should `frappe_ai` ship that migration?

## The decision

**No migration patches and no period where both apps work on the same data.** `frappe_ai` is built from scratch. `flow` was only the description of what features to build. When `frappe_ai` covers what `flow` did, `flow` is uninstalled and its data discarded.

Out of scope: copying `Flow *` rows into `AI *` DocTypes, moving Password values (they are stored apart from the DocType row and are not carried by a rename), migrating the vector store, and any runtime link between the two apps.

## What follows

**Good**
- Much less work and risk: Password handling, link repointing across 16 DocTypes, and re-indexing would be a project of their own.
- Freedom to change fields (for example the single `AI Settings` replacing `Flow Knowledge Settings`) without carrying old shapes.
- No dual-read or dual-write period.

**Costs**
- Existing `flow` agents, tools, sessions, runs, knowledge bases and memories do not carry over. Agents and knowledge bases are recreated by hand and knowledge is re-embedded.
- `Flow Run` history disappears with the app. Export it first if anyone needs it for audit.
- After uninstalling `flow` there is no way back except reinstalling it.

Both apps can be installed together during development (the DocType names do not collide), but that is not a supported production setup.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Build, then migrate, then retire | Preserves history but the cost is not justified when the data in `flow` is not worth keeping |
| Keep both permanently | Two agent systems, two tool registries, two chat panels and two wildcard document hooks on every save |
| Rename `flow` in place | The runtime is replaced wholesale, so a rename would carry the history of code that is being deleted |

## Before you uninstall `flow`

- [ ] No production agent in `flow` is still in use.
- [ ] Any `Flow Run` history needed for audit is exported.
- [ ] Knowledge sources are listed so they can be recreated.
- [ ] Custom `Flow Tool` scripts are copied into `AI Tool` rows (or rebuilt as Assistant Core tools).
- [ ] Each `Flow Trigger` has an `AI Trigger` and has been seen firing.
- [ ] Provider API keys are at hand (Password fields cannot be read back).
- [ ] There is a database backup. Uninstalling drops the tables.

## How we check

`frappe_ai` installs on a site with no `flow` data, works without it, and `bench uninstall-app flow` leaves it working.

## Related

[002 Feature map](../specifications/002-feature-mapping.md), [progress notes](../progress/).
