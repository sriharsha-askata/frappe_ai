# ADR 0015 — configuration-time model capability tests

**Status:** Accepted
**Date:** 2026-08-23

## Context

A single Test Connection ping proved only that one provider request could be
made. It did not cover the streaming, function-calling, structured-output, or
embedding behavior that the agent and knowledge paths actually depend on. A
runtime preflight would add latency and duplicate provider calls for every run.

## Decision

Keep testing explicit and configuration-time: the saved AI Model form invokes a
fresh capability suite on each click. Chat checks reuse the OpenAI-compatible
Agno transport used by runtime execution. Embedding checks use the corresponding
OpenAI SDK endpoint. The suite executes only a synthetic no-op tool and returns
structured per-check results.

Core capabilities are strict. Structured output and the bounded larger-input
probe are warnings because provider support and practical limits vary. A base
configuration/authentication failure blocks dependent checks. Runtime execution
never calls Test Connection and has no execution gate based on its result.

## Consequences

The form gives operators actionable capability information without caching a
stale status. A successful suite still cannot guarantee production success:
provider outages, quota changes, retired models, and later prompt-size changes
remain runtime concerns. The suite cannot validate real business tools because
it intentionally never executes them.
