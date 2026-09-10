# Knowledge retrieval returns chunks without any permission check

**Type:** Bug / Security gap
**Severity:** High
**Status:** Open
**Source:** Production readiness review, 2026-09-10 (Finding F-10)

---

## Description

The entire `frappe_ai/knowledge/` module — `retriever.py`, `store.py`,
`knowledge.py`, `attachment_store.py` — contains **no** `frappe.has_permission`
call and no permission-respecting `frappe.get_list` call. Retrieval is scoped to
the agent's configured knowledge bases and to nothing else.

Where a knowledge base was ingested from Frappe documents, the chunks carry that
document's content but no permission linkage. `search_knowledge` therefore
returns text from documents the acting user has no right to read.

## Why it needs to be done

This is the one place where [ADR 0003](../decisions/0003-tools-execute-in-frappe.md)'s
invariant —

> The FastAPI service can never cause an action the acting user could not have
> performed themselves in the desk.

— is broken *inside* Frappe rather than at the MCP boundary. Tool dispatch
faithfully calls `frappe.set_user(acting_user)`, but the knowledge tool does not
consult permissions once it is there, so acting as the right user changes nothing.

Critically, **this needs no prompt injection and no misconfiguration.** It is the
normal behaviour of the feature: a user asks a question, and the agent answers
using content they cannot open. It will not appear in any audit as an access
violation, because no access check was ever performed to fail.

The exposure scales with the usefulness of the knowledge base. The more valuable
the corpus, the more likely it spans documents with differing visibility.

## Fix

Record source identity on each chunk at ingestion time — at minimum
`source_doctype` and `source_name`, which the ingestion path already knows —
then filter retrieved chunks for the acting user before they reach the model.

Two viable shapes:

- **Post-filter.** Retrieve top-N from LanceDB, then drop chunks failing
  `has_permission`. Simple and correct, but over-retrieval is needed to avoid a
  thin result set after filtering, and permission checks are per-document.
- **Pre-filter.** Resolve the set of readable source documents first and pass it
  as a LanceDB filter. Better result quality, but the readable set can be large,
  and it needs care not to become its own N+1.

Post-filtering is the safer first move: it fails closed, and it can ship without
changing retrieval ranking behaviour.

Chunks with no document provenance (e.g. ingested files not attached to a
DocType) need an explicit policy rather than an implicit pass — inherit the
visibility of the `AI Knowledge Base` they belong to.

## Trade-offs

Filtering costs latency on every retrieval, and a permission-heavy site will feel
it. That cost is not optional: the alternative is disclosure. Caching the
readable-source set per user per run is a reasonable optimisation once correct.

## Verification

- A user without read permission on a DocType asks a question whose answer lives
  only in a chunk ingested from that DocType. The agent must not surface the
  content, and must say it found nothing rather than inventing an answer.
- The same question from a user *with* permission returns the content.
- Chunks lacking provenance follow the knowledge base's own visibility.
- Ingestion records `source_doctype`/`source_name` for DocType-sourced chunks.
