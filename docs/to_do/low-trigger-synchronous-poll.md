# Trigger runs synchronously poll the FastAPI stream inside an RQ worker

**Type:** Improvement (reliability / resource usage)
**Severity:** Low
**Status:** Open — flagged as a question, not yet scoped as a concrete fix
**Source:** Architecture review, 2026-09-06 (Finding L-1)

---

## Description

Trigger-driven runs (`frappe_ai/triggers/triggers.py`) do not just "fire and record
a result later." `_run_via_service(run, session, user)` (`triggers.py:308`) drives
the run through the same FastAPI SSE stream as interactive chat, and
`_wait_for_terminal_run_status` (`triggers.py:429`) **synchronously polls that
stream to completion from inside the RQ background job**.

## Why it needs to be done

This ties up an RQ worker slot for the entire duration of a potentially
multi-minute agent run — structurally the same "worker held for the LLM call"
problem the Frappe/FastAPI process split was built to solve for interactive chat
([ADR 0001](../decisions/0001-agno-fastapi-over-frappe-native.md)), just relocated
from the gunicorn worker pool to the RQ worker pool.

Whether this actually matters in practice depends on RQ worker pool sizing and
trigger volume — it was not asserted as a confirmed defect in the review, only
flagged as worth a deliberate look. A site with few triggers and a generously
sized RQ pool may never notice; a site with many high-volume triggers could see
trigger throughput degrade the same way interactive chat used to.

## Next step

Not yet a concrete fix — first confirm whether this is actually biting any real
deployment (check RQ worker utilization during trigger-heavy periods) before
deciding whether to invest in an async/non-blocking trigger-run mechanism.

## What happens if we do nothing

No correctness impact — triggers still complete and their results are still
recorded correctly (per [ADR 0007](../decisions/0007-failure-over-durable-execution.md),
trigger runs are durable via RQ's at-least-once delivery). The only risk is worker
throughput under load, which is unconfirmed as an actual problem today.
