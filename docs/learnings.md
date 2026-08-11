# AI Learnings

Working notes on things learned while building `frappe_ai` that aren't formal
specs, decisions, or progress tracking — the "wait, why doesn't this work the way
I expected" moments, kept here so they don't have to be rediscovered.

Unlike `decisions/` (immutable once Accepted) and `specifications/` (current
intended behaviour), this file is allowed to just be a running log. Newest first.

---

## Confirmation pause/resume: three real bugs, found only by live-testing all three answer types

**Date:** 2026-08-07 (closing out Phase 3's last unverified completion criterion)

### The issue

Phase 3's completion criteria listed "a `create` tool call pauses for confirmation;
Approve/Deny/redirect all behave per spec" as unit-tested but not live-verified —
the earlier live smoke test only exercised `find_doctypes` (no confirmation
needed). Closing that gap meant actually driving a real model through all three
`resume_run` answer types (Approve, Deny, free-text redirect) against a real
tool that requires confirmation (`create`). All three surfaced real bugs; none
of them were caught by the unit test suite (148/155 passing throughout), because
unit tests exercised `PendingConfirmation` collection and message-shape helpers
in isolation, never a full pause → resume → real-model-continuation round trip.

### Bug 1 — the paused transcript poisoned the model on resume

**Symptom:** approving a pending call didn't make the tool actually run. The
model, on resume, fabricated a plausible-looking fake success message instead.

**Cause:** `_messages(final_output)` (`chat.py`) serialized *every* message from
Agno's `RunOutput`, including the `role: "tool"` row Agno's own error-handling
path produces for a call that raised `PendingConfirmation` — whose `content` is
the internal `PENDING_CONFIRMATION_MARKER` string, not a real result. This got
persisted into `AI Session Message` verbatim. On resume,
`AISession.build_prompt_messages()` faithfully replayed that marker text back to
the model as if it were a genuine tool result — the model read "this call
already returned something" and treated it as done, rather than "this call is
still awaiting approval."

**Fix:** `_messages_excluding_pending(output, pending)` — strips both the
marker-content tool message and the assistant `tool_calls` message that
requested it (if that assistant turn requested *only* pending calls) from what
gets persisted on a Paused segment. The stored transcript now ends cleanly at
the last real exchange instead of carrying a poisoned tool result forward.

### Bug 2 — approving a call didn't actually make anything happen

**Symptom:** even after fixing Bug 1 (so the model no longer hallucinated), the
model's *next* turn had no way to know it should call `create` again — nothing
in the resumed prompt told it "the call you tried is now approved, go ahead."
`approved_call_ids` on `AgentBuilder`/`Function.entrypoint` only gates whether a
call *the model re-requests* dispatches instead of raising `PendingConfirmation`
again — it does nothing to make the model re-request that exact call, and after
Bug 1's fix, there was no longer even a stale assistant `tool_calls` message left
in the transcript to prompt a re-request.

**Root design gap:** this is exactly what `flow`'s own resume
(`lib/agent.py`'s `_prepare_resume`) does differently — on `"Approve"`, it
**actually runs the tool and serializes the result** directly, rather than
hoping the model asks again. Phase 3's original implementation only gated
*whether* a re-request would succeed; it never actually issued one.

**Fix:** `_dispatch_approved()` — on resume, before building the Agno agent's
`input`, directly dispatch each approved call via `FrappeClient.dispatch_tool`
using the arguments `AI Run.questions` recorded when the run paused (now also
returned by `get_run_config`, since the service needed a way to see the prior
segment's pending-call arguments). The assistant tool-call request and its real
result are then reconstructed as `Message` objects and injected into the
model's `input` for this turn only (`_to_agno_messages`'s
`approved_results` param) — giving the model the context it needs to continue,
without ever having stored the poisoned version. `tool_started`/`tool_ended` SSE
events are emitted for the direct dispatch too, so the panel sees it as a normal
tool call.

### Bug 3 — Deny silently dropped its own audit record

**Symptom:** denying a call correctly halted the run (no tool executed, correct
`Completed` status) — but the persisted transcript ended up **missing the
denial entirely**, just `system → user`, no record that a call was ever denied.

**Cause:** `AIRun.apply_result` documents `messages` as the segment's **full**
transcript (used to diff against the session's already-stored row count via
`_new_messages_for_session`'s `full_transcript[existing_count:]`).
`_denied_result`'s first fix (prepending the prior transcript to the denial
rows) filtered the `system` role out of `prior_messages` before prepending —
reasonable-looking, since other paths in this file also treat `system` as
special — but `_new_messages_for_session`'s `existing_count` is a literal row
count against `AI Session Message`, which **does** include the stored system
row. Filtering it out shifted every subsequent index by one, and the slice
silently dropped the reconstructed assistant message, leaving only the
`tool`-role denial row — which is itself a symptom of the fix landing
*half*-correct on the first attempt (the assistant/tool pairing for the denied
call also needed reconstructing, same reasoning as Bug 2's fix, and was added
in the same pass).

**Fix:** `_denied_result` passes `prior_messages` through unfiltered (system row
included), matching exactly what's actually stored, and reconstructs the paired
assistant `tool_calls` message for each denied call (from `questions_by_id`)
before the `role: "tool"` `{"status": "denied"}` row, mirroring `_dispatch_approved`'s
message-pairing for the same reason: a bare `tool`-role message with no
matching assistant request in the transcript is invalid shape for a future
resume to replay to the model.

### A known, deliberately unfixed cosmetic issue

After the Bug 2 fix, a live-verified Approve transcript looked like:
`system → user → user (duplicate) → assistant(tool_calls) → tool(result) →
assistant(final)` — the `user` row appears twice. Agno's `RunOutput.messages`
echoes back the full input it was given (including the stored `user` row,
re-sent as part of `input` for the resume's model call) rather than only new
content, and `_build_result`'s `prefix + _messages(final_output)` doesn't
currently account for that overlap. The duplicate is inert — identical content,
doesn't confuse a subsequent turn, doesn't break `_new_messages_for_session`'s
counting (both copies get counted and stored, consistently) — so it was left as
a known cosmetic gap rather than chased further, given the three bugs above
were the ones actually breaking correctness. Worth a proper fix before Phase 6's
frontend renders transcripts to a human, but not blocking.

### Verification

All three answer types live-verified end-to-end against the real Groq-via-
OpenAI-compat setup from the provider-resolution fix above, each with a fresh
session:

- **Approve:** paused on `create`, approved, tool actually dispatched
  (`{"doctype": "ToDo", "created": [...]}`), real `ToDo` record confirmed to
  exist in the DB with the correct description, transcript correctly paired.
- **Deny:** paused on `create`, denied, run completed immediately with no
  model call (`iterations: 0`), confirmed **no** `ToDo` record was created,
  transcript correctly shows the denial paired with its assistant request.
- **Redirect:** paused on `create` with a wrong description, sent free-text
  feedback asking for a specific correction — the model read the feedback,
  investigated with `read`, then retried `create` with exactly the corrected
  description; approved that retry; confirmed exactly **one** `ToDo` exists in
  the DB, with the corrected (not the original) description — proving the
  redirect feedback was genuinely understood and acted on, not just accepted
  and ignored.

Two live runs also hit transient/model flakiness unrelated to this fix (a
one-off Frappe-connectivity blip, and Groq's small model twice attempting to
call a hallucinated tool name) — both correctly resulted in `fail_run`/`Failed`
with a real error message and no crash, which is itself a (re-)confirmation
that the existing error-handling path (from the earlier provider-fix session)
remains solid under real-world model unreliability, not just clean-path testing.

Full `frappe_ai` suite after all three fixes: 155 tests, 148 passing — same 7
pre-existing failures as every prior check in this project, no regressions.

This closes Phase 3's last open completion criterion.

---

## Agno needs a real provider SDK per provider — it does not replace them

**Date:** 2026-08-06 (during Phase 3 live-testing)

### The issue

While live-testing Phase 3's chat run loop, the plan was to use a real API key
(a working Groq key, borrowed from `apps/flow`'s `Flow Provider` "groq" record)
to prove a full successful model call end-to-end. This failed immediately:

```
ImportError: `groq` not installed. Please install using `pip install groq`
```

The assumption going in was that switching to Agno (ADR 0001, ADR 0009) meant
provider support was "built in" — that Agno itself talks to each provider's API,
the way `frappe_ai` talks to Frappe. That assumption was wrong.

### Why it happens

Checked directly in the installed package
(`env/lib/python3.12/site-packages/agno/models/groq/groq.py` and
`agno/models/anthropic/claude.py`): every `agno.models.<provider>` class is a
thin wrapper around that provider's **own official SDK**.

```python
# agno/models/groq/groq.py
try:
    from groq import APIError, APIResponseValidationError, APIStatusError
    from groq import AsyncGroq as AsyncGroqClient
    from groq import Groq as GroqClient
    ...
except ImportError:
    raise ImportError("`groq` not installed. Please install using `pip install groq`")
```

Agno gives one consistent Python interface (`Model.response()`,
`Model.aresponse()`, etc.) across providers, but under the hood it still needs:

- `openai` for `agno.models.openai.OpenAIChat`
- `anthropic` for `agno.models.anthropic.Claude`
- `groq` for `agno.models.groq.Groq`
- `google-genai` for `agno.models.google.Gemini`
- ...one SDK per provider slug in `frappe_ai.lib.model.PROVIDER_MODEL_CLASSES`

This bench had only `openai` installed. Every other provider slug `frappe_ai`
claims to support (`anthropic`, `groq`, `gemini`, `mistral`, `cohere`, ...) would
hit the same `ImportError` the first time someone actually tries to call it,
even though `AI Provider`/`AI Model` happily validate and save.

This is actually already documented, just easy to miss: `lib/model.py`'s own
comment on `PROVIDER_MODEL_CLASSES` says the slug→class map is "checked by
module existence only ... never imported at validation time ... The provider's
own SDK is only required when a model is actually instantiated." Phase 1's
`test_connection()` is the only place that ever surfaces this gap before now —
Phase 3's live run loop is the second.

### Why this wasn't caught earlier

`AI Provider`/`AI Model` validation (`is_known_provider()`) only checks that the
provider slug is one of the 21 Agno maps in `PROVIDER_MODEL_CLASSES` — a plain
dict membership check, no import. A model can be fully configured and saved with
`provider="anthropic"` on a bench that has never installed the `anthropic`
package. The gap only surfaces at the moment of an actual call
(`test_connection()`, or now, a real chat run) — which is exactly the kind of
thing unit tests with mocked/fake keys don't exercise, since they never get far
enough to hit the real `import`.

### What was tried and rejected before landing on the real fix

1. **Reuse the existing `AI Provider(name="openai")` row, temporarily overwrite
   its `base_url` to Groq's OpenAI-compatible endpoint** (`api.groq.com/openai/v1`),
   since Groq's API is wire-compatible with OpenAI's and `openai` was already
   installed. **Rejected** — this makes a doc named "openai" silently mean
   "actually Groq," which is misleading state left behind (or, if reverted after
   the test, a hack rather than a real fix) — flagged directly by the user as
   not acceptable, correctly.
2. **Create a second, distinctly-named `AI Provider` row also pointed at the
   OpenAI-compatible endpoint.** **Blocked structurally** — `AI Provider`'s
   `autoname` is `field:provider` (lowercased), and `provider` is `unique`. Since
   the Agno class lookup is keyed by the *slug itself* (`"openai"` →
   `OpenAIChat`), a second row can't be named anything other than `openai`
   without breaking the very lookup that made it useful. There is structurally
   no way to have two `AI Provider` rows both resolve to `OpenAIChat` under
   different identities.
3. **`pip install groq`.** Would have worked cleanly and is the "correct" fix in
   isolation, but the user pushed back on the premise: *"it is not ideal to
   install [a] corresponding sdk to use the api key of a provider"* — i.e. the
   goal was specifically to avoid needing N SDKs installed for N providers a user
   might configure, which is a real, reasonable operational goal for a
   multi-tenant/many-provider admin panel, not a workaround to route around.

### How `flow` avoids this same wall

`flow` doesn't hit this problem at all, and understanding *why* is what led to
the actual fix. `flow` uses **litellm**, not per-provider SDKs. litellm needs
no vendor SDK per provider — it speaks each provider's REST protocol directly
itself, routing purely by a **string prefix on the model id**
(e.g. `openai/llama-3.1-8b-instant`, `anthropic/claude-...`) plus an optional
per-model `api_base` override:

```python
# flow/flow/doctype/flow_model/flow_model.py
base_url = self.base_url or provider_creds.get("base_url")
...
if base_url:
    kwargs["api_base"] = base_url
```

Critically, a `Flow Model` can be **completely unlinked** from any
`Flow Provider` row and still work against any OpenAI-wire-compatible endpoint —
Groq, OpenRouter, a local vLLM server, anything — because litellm's routing
never depended on a `Flow Provider` document existing. The provider row is a
credential-sharing convenience, never a hard requirement to make a model callable.

`frappe_ai` dropped litellm on purpose (ADR 0009) — a deliberate, already-decided
trade: one fewer abstraction layer under Agno, at the cost of losing litellm's
"any OpenAI-compatible endpoint, zero SDKs, just a base_url" flexibility. This
was known and accepted as a trade-off in the abstract when ADR 0009 was written;
this session is the first time it was actually *felt*, trying to point at a real
non-default endpoint on a bench without every SDK installed.

### The actual fix (not yet implemented — scoped, pending)

The `AI Model` DocType already has the two fields `flow` uses for this
(`api_key`, `base_url`) sitting unused for this purpose in `frappe_ai`, because
`_model_call_config` (`frappe_ai/api/service.py`) currently *requires*
`model.provider` to resolve to a class, conflating two separate jobs into one
field:

1. **Which Agno Python class to instantiate** — this is what `provider` is
   actually for, and it's mandatory; Agno needs a concrete class, unlike
   litellm's string-prefix routing.
2. **Which real HTTP endpoint that class talks to** — this is what `base_url`
   is for, and it's already optional/overridable, independent of #1.

The fix is to stop requiring an `AI Provider` **document** to exist merely to
resolve #1. `provider` on `AI Model` should be enough by itself — a plain slug
choice, validated against `is_known_provider()` exactly as it is today — to
pick the Agno class, whether or not a matching `AI Provider` row happens to
exist. `AI Provider` stays what it always was meant to be: an optional
convenience for sharing one `api_key`/`extra_params` across several models of
the same provider slug, not a gate on whether any model can be called at all.

Concretely: `AI Model(provider="openai", base_url="https://api.groq.com/openai/v1",
api_key=<groq key>)` should just work — `agno.models.openai.OpenAIChat` (already
installed) talking to Groq's OpenAI-wire-compatible endpoint, zero new SDKs,
no `AI Provider` row required or repurposed. Same shape `flow` already has,
minus the string-prefix trick litellm used, because Agno needs the explicit
class instead.

This directly unblocks live-testing Phase 3's success path (a real streamed
completion) using the already-installed `openai` SDK against Groq's real API,
which was the original goal before this detour.

### Alternatives considered and where they stand

| Approach | Needs new SDK? | Misleads existing state? | Structurally possible? | Verdict |
|---|---|---|---|---|
| Reuse/overwrite existing `AI Provider("openai")`'s `base_url` | No | Yes | Yes | Rejected — misleading |
| Second `AI Provider` row under a different name, same slug | No | No | **No** — `unique`/`autoname` collision | Not possible as designed |
| `pip install groq` (and eventually N more SDKs) | Yes | No | Yes | Rejected — defeats the "no per-provider SDK" goal |
| Reintroduce litellm | No | No | Yes | Rejected — already decided against in ADR 0009; would re-add the exact abstraction layer that decision removed |
| **Decouple `AI Model.provider`'s class-resolution from requiring an `AI Provider` doc; keep `base_url` as the real endpoint override** | No | No | Yes | **Chosen** — mirrors `flow`'s existing unlinked-model pattern; smallest change; doesn't reopen ADR 0009 |

### Resolution — implemented and verified live (2026-08-07)

The fix landed as a **schema change**, not just a logic change, once it became
clear why the old code path failed even though `_model_call_config` and
`resolve_provider_credentials` already degraded gracefully when no `AI
Provider` document existed: `AI Model.provider` was a `Link` field
(`options: "AI Provider"`). Frappe validates Link fields against the target
doctype's row existing **at save time**, independent of anything the
controller's own `validate()` checks — so `AI Model(provider="anything-not-yet-a-real-AI-Provider-row")`
was rejected by Frappe core before `_validate_provider_known()` ever ran. The
application-level guards (`resolve_provider_credentials` returning `{}` for a
missing doc, `_model_call_config` only requiring the string be non-empty) were
already correct; the DocType's field type was the actual blocker.

**Confirmed safe to change** by checking every use of `model_doc.provider`
across the codebase first: every call site (`is_known_provider(self.provider)`,
`get_model_class(self.provider)`, `resolve_provider_credentials(self.provider)`)
already treated it as a bare slug string, never through Link-specific semantics
(no `frappe.get_doc` navigation via the field, no reliance on
`show_title_field_in_link` or similar). So the field could change type freely
with zero consumer-side changes.

**What changed:**

- `AI Model.provider`: `Link → AI Provider` → `Autocomplete` (free text, no
  target doctype). Mirrors the existing `model_id` field's own pattern exactly
  — `model_id` was already `Autocomplete` for the analogous "don't require a
  matching Link-validated row to exist" reason (`ai_model.js`'s
  `ignore_validation = true` comment: "suggestions are hints, never a
  restriction"). Validation is unchanged: `_validate_provider_known()` still
  rejects any slug not in `PROVIDER_MODEL_CLASSES`, just via a normal
  `ValidationError` now instead of Frappe core's `LinkValidationError`.
- `AI Model.api_key`/`base_url`: dropped their `depends_on: eval:!doc.provider`
  (previously hidden whenever a provider was linked). Now always visible/settable
  — a model can have both a `provider` slug **and** its own credentials
  overriding an `AI Provider`'s (or standing in for a nonexistent one).
- `AIModel.test_connection()`, `_validate_provider_known()`,
  `_model_call_config()` (`api/service.py`): error messages updated from
  "requires a **linked** Provider" to "requires a Provider **slug**" — the
  logic itself needed no change, only wording that had assumed Link semantics.
- Two Phase-1 tests needed fixing, both revealing real (if minor) test debt
  rather than new bugs: `test_unknown_provider_rejected` asserted
  `frappe.LinkValidationError` (now correctly `frappe.ValidationError` with a
  more useful message — Frappe core's generic Link error was strictly worse);
  `test_extra_params_parsed` used the real-world slug `"groq"` as its fixture
  name, which collided with actual `groq`-provider setup work done earlier in
  this same session outside that test's transaction — renamed the fixture to
  `"fireworks"` (a slug nothing else in this session touches).

**Verified live**, using the real Groq API key (borrowed from `apps/flow`'s
`Flow Provider "groq"` record) routed through Groq's OpenAI-wire-compatible
endpoint (`https://api.groq.com/openai/v1`) via the already-installed `openai`
SDK — `AI Model(provider="openai", base_url="https://api.groq.com/openai/v1",
api_key=<groq key>)`, zero new SDKs, no `AI Provider("openai")` row touched.
Full success path confirmed end-to-end against a standalone test service
instance: `run_started` → real `tool_started`/`tool_ended` (a genuine
`find_doctypes` call through `dispatch_tool`, permission-checked, executed in
Frappe) → real streamed `text` deltas (18 separate chunks) → `done` with
correct `output`, `iterations: 2`, and real token usage
(`{"prompt_tokens": 2660, "completion_tokens": 90, "total_tokens": 2750}`).
`AI Run` and the session's `AI Session Message` transcript were both confirmed
persisted correctly (system → user → tool → assistant, in order).

Full `frappe_ai` suite after the fix: 155 tests, 148 passing — same 7
pre-existing `ignore_user_permissions` failures as documented in Phase 1/3, no
regressions.

**Still not live-verified:** a genuine confirmation pause/resume cycle (the
prompt used for this test only exercised a non-confirming tool,
`find_doctypes`; a `create`/`update`/`delete`/`run_action`/`execute` call would
be needed to trigger `PendingConfirmation` for real). Left as the next thing to
verify live, now that the provider blocker is gone.

### Alternatives considered and where they stand

| Approach | Needs new SDK? | Misleads existing state? | Structurally possible? | Verdict |
|---|---|---|---|---|
| Reuse/overwrite existing `AI Provider("openai")`'s `base_url` | No | Yes | Yes | Rejected — misleading |
| Second `AI Provider` row under a different name, same slug | No | No | **No** — `unique`/`autoname` collision | Not possible as designed |
| `pip install groq` (and eventually N more SDKs) | Yes | No | Yes | Rejected — defeats the "no per-provider SDK" goal |
| Reintroduce litellm | No | No | Yes | Rejected — already decided against in ADR 0009; would re-add the exact abstraction layer that decision removed |
| **Decouple `AI Model.provider`'s class-resolution from requiring an `AI Provider` doc; keep `base_url` as the real endpoint override** | No | No | Yes | **Chosen and implemented** — mirrors `flow`'s existing unlinked-model pattern; smallest change; doesn't reopen ADR 0009 |

### Open follow-up

- Live-verify a real confirmation pause/resume cycle (Approve/Deny/redirect) —
  the one piece of Phase 3's completion criteria still not exercised against a
  real model call.
- Consider whether this is worth a short ADR of its own, or is better folded
  into ADR 0009 as an amendment/clarification — it's a real design refinement
  to a decision already on the books (provider resolution no longer requires a
  linked document), not a new architectural decision.
