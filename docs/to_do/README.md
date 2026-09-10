# To Do

Actionable items surfaced by review — bugs, improvements, and gaps — tracked
individually so each can be discussed, approved, and closed on its own. Unlike
`decisions/` (immutable once accepted) and `specifications/` (current intended
behaviour), items here are expected to be deleted once resolved; they are work
items, not a permanent record. If a to-do reveals something worth keeping as
history, promote that into a `decisions/` ADR or `progress/` note when it's
closed, per this repo's documentation conventions.

**Naming:** `severity-slug.md` — filename leads with severity so priority is
visible in a plain directory listing, not buried in file content.

**Every file states:** what it is (bug / improvement / new feature), severity,
why it needs to be done, and a description. Fix/next-step, trade-offs, and
verification are included where already known; left open where a decision is
still pending.

---

## Open items

| File | Type | Severity | One-line summary |
|---|---|---|---|
| [high-mcp-budget-bypass.md](high-mcp-budget-bypass.md) | Bug / security gap | High | Remote MCP tool calls bypass all per-run execution budgets |
| [high-chatpy-confirmation-extraction.md](high-chatpy-confirmation-extraction.md) | Improvement | High | Confirmation pause/resume logic should move out of the 860-line `chat.py` |
| [medium-tool-migration-telemetry.md](medium-tool-migration-telemetry.md) | Improvement | Medium — do early | No usage data distinguishing legacy vs. FAC tool-call volume |
| [medium-correlation-id.md](medium-correlation-id.md) | Improvement | Medium | No correlation ID ties a run's Frappe and FastAPI logs together |
| [medium-error-truncation-inconsistency.md](medium-error-truncation-inconsistency.md) | Improvement | Medium | Error-truncation limit duplicated at 500/300 across five files |
| [low-trigger-synchronous-poll.md](low-trigger-synchronous-poll.md) | Improvement | Low | Trigger runs synchronously poll the FastAPI stream inside an RQ worker |
| [low-lancedb-reconnect-per-call.md](low-lancedb-reconnect-per-call.md) | Improvement | Low | LanceDB connection re-opened on every single knowledge operation |

All items originate from the architecture review of 2026-09-06. See that review's
full write-up for the tracing and evidence behind each entry; these files carry
only what's needed to act on each one independently.

## Sequencing note

Severity is not the same as order-of-work. `medium-tool-migration-telemetry.md`
is flagged to land early despite Medium severity, because it's cleanup for a
migration that other items here (e.g. the `chat.py` extraction) touch adjacent
code for — see that file's Priority note.

The MCP budget item (`high-mcp-budget-bypass.md`) needs an explicit design
decision before implementation and should not be bundled with any other item —
see its Fix section.
