# Wildcard `doc_events` puts frappe_ai in the path of every write on the site

**Type:** Improvement / Performance
**Severity:** Medium
**Status:** Open
**Source:** Production readiness review, 2026-09-10 (Finding F-14)

---

## Description

`frappe_ai/hooks.py` registers document events against `"*"`:

```python
doc_events = {
	"*": {
		"after_insert": "frappe_ai.triggers.dispatch",
		"on_update": "frappe_ai.triggers.dispatch",
		"on_submit": "frappe_ai.triggers.dispatch",
		"on_cancel": "frappe_ai.triggers.dispatch",
		"on_trash": "frappe_ai.triggers.dispatch",
	}
}
```

Every insert, update, submit, cancel and delete of **every DocType on the site**
therefore calls into `frappe_ai`. `dispatch` filters quickly and excludes
frappe_ai's own internal DocTypes, but the filtering itself is the cost, and it
is paid on every write.

## Why it needs to be done

This is a bench-wide tax levied by one app. On a site running ERPNext or other
write-heavy apps, the overwhelming majority of these calls will never match a
trigger — `AI Trigger` rows typically cover a handful of DocTypes, not all of
them.

The cost is small per write and invisible in isolation, which is exactly why it
deserves attention: it shows up as a diffuse slowdown in bulk imports, migrations
and batch jobs, attributed to whichever app happens to be doing the writing
rather than to the app imposing the overhead.

It also widens the blast radius. A bug or an unhandled exception in `dispatch`
becomes a bug in **saving any document on the site**, including for users and
apps with no AI involvement at all. That coupling is disproportionate to the
feature it serves.

## Fix

Resolve the set of DocTypes that actually have enabled `AI Trigger` rows, and
register events only for those.

Frappe evaluates `doc_events` from the hooks cache, so the set must be refreshed
when triggers change — `AI Trigger`'s own `on_update`/`on_trash` can clear the
relevant cache key, and `after_migrate` can rebuild it. The wildcard remains the
correct fallback only if that resolution is unavailable at hook-load time.

If dynamic registration proves impractical within Frappe's hook model, the
cheaper alternative is to keep the wildcard but make the very first check a
cached set-membership test on the DocType name, so the non-matching path costs a
single hash lookup and nothing else — no document inspection, no query.

## Trade-offs

Dynamic registration adds a cache-invalidation path, which is a new way to be
subtly wrong: a stale cache means a trigger silently stops firing. That failure
mode is worse than the performance cost it fixes, so the invalidation must be
covered by tests before this ships — see also the class of bug in Finding F-5,
where a trigger failing silently went unnoticed.

## Verification

- Saving a document of a DocType with no `AI Trigger` performs no frappe_ai
  query and no document inspection.
- Creating an `AI Trigger` for a DocType causes writes to that DocType to be
  dispatched without requiring a restart or migrate.
- Disabling or deleting the last trigger for a DocType stops dispatch for it.
- Bulk-inserting N documents of an untriggered DocType shows no measurable
  frappe_ai overhead.
