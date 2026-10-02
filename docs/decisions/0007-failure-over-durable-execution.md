# ADR 0007 — A restart fails a run cleanly; runs are not resumed mid-way

**Status:** Accepted · **Date:** 2026-08-05

## The problem

With the agent loop in a separate service ([ADR 0001](0001-agno-fastapi-over-frappe-native.md)), a restart of that service (deploy, crash, out of memory) can end many runs at once, and the browser's stream dies without Frappe necessarily knowing why. A production review asked for **durable execution**: save the state after each step so a restarted service can pick the run up where it stopped.

## The decision

**A run that is cut off fails cleanly. It is not resumed.** The user retries, which starts a new run in the same session. The conversation history is kept (messages are saved in Frappe as they are produced), so only the unfinished turn is lost.

Three mechanisms make the failure visible instead of silent:

| Mechanism | What it does |
|---|---|
| Stale check (`RUNNING_STALE_SECONDS = 300`) | When the user sends the next turn, a run still *Running* after 300 s is marked Failed instead of blocking the session |
| `recover_session` | When a session is reloaded, any run still *Running* is marked Failed |
| `stop_run` | The user can end a run explicitly |

The service also marks a run Failed itself when the browser disconnects or an error is caught.

**Note:** these run when someone touches the session. There is no background sweeper, so an abandoned run can stay *Running* in the database until its session is used again. A scheduled sweeper would be a simple improvement ([010](../specifications/010-review-topics.md)).

Trigger runs are started from background jobs (`frappe.enqueue(..., enqueue_after_commit=True)`), so they get Frappe's normal job handling. I could not verify redelivery behaviour after a worker crash from the code alone; check the job queue's settings if this matters to you.

## What follows

**Good**
- No checkpointing machinery. The real difficulty is not storing state but **not repeating side effects**: resuming after a tool call whose result was not recorded either re-runs it (duplicate records, emails, submissions) or skips it (lost work). That needs every tool to be idempotent or journaled.
- Retrying a chat is cheap: a few seconds and one model call.
- The service stays stateless, so scaling is just adding instances.

**Costs**
- A long run killed near the end starts over, wasting tokens and time. A retry pays again for the whole conversation so far.
- Deploying mid-conversation fails those runs; drain connections first and deploy off-peak.
- The 300 s window is a setting, not a law: it is also how long a dead run can look *Running* before being cleaned up.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Checkpoint and resume mid-run | The side-effect problem above, across every tool, for a rare event. Revisit if long unattended runs become the main workload |
| Send all runs through background jobs (RQ) | Brings back a fixed worker pool and breaks direct streaming ([ADR 0004](0004-sse-direct-from-fastapi.md)) |
| An external workflow engine (Temporal, Restate) | A large piece of infrastructure for one workload |

## How we check

- Stop the service during a run: the run is marked Failed, the session stays usable, and a retry starts with the earlier messages intact.
- Reload a session that has an orphaned *Running* run: `recover_session` fails it and reports the count.
- No run stays *Running* forever once its session is used again.

## Related

[001 §8](../specifications/001-architecture.md), [ADR 0004](0004-sse-direct-from-fastapi.md).
