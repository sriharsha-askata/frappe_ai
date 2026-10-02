# Learnings

Short lessons from building `frappe_ai`: things that did not work the way we expected, why, and what fixed them. They are here so nobody has to rediscover them. This is a running log (newest first), not a record of decisions; decisions live in [`decisions/`](decisions/) and current behaviour in [`specifications/`](specifications/).

Each entry: **what happened → why → what we did.**

---

## A slow tool-calling stream looked like an empty "provider error" (2026-08-30)

**What happened.** A long review run failed on its third model call with `model_provider_error` and an empty message. Token limits were a false lead: the first two calls succeeded, and the third returned HTTP 200 and streamed for about 137 seconds before dying.

**Why.** The HTTP client's read timeout was 60 s, independent of `AI Settings.stream_timeout` (600 s). Agno wrapped the resulting empty `ReadTimeout` as an error with no message.

**What we did.** The client timeout now defaults to 600 s (and follows `AI Settings.stream_timeout`), and a stream timeout is reported as "Provider stream timed out after N s…" instead of an empty error (`_ainvoke_stream_with_timeout_context` in `service/routes/chat.py`). The same run then completed.

**Lesson.** When a provider error has an empty message, suspect a timeout before suspecting the model. Keep client timeouts in step with the stream timeout you advertise.

---

## Approve, Deny and redirect each had a bug that unit tests missed (2026-08-07)

Only a real pause-and-resume with a real model, for all three answer types, found these. The unit tests checked helper functions in isolation.

**1. The paused transcript poisoned the model.** The saved conversation included the internal "pending confirmation" marker as if it were a real tool result. On resume the model read it as "this call already returned something" and invented a success message. *Fix:* when a run pauses, do not save the marker message or the assistant message that asked only for pending calls (`_messages_excluding_pending` in `chat.py`).

**2. Approving did not run anything.** Approval only allowed a call *if the model asked again*, but nothing prompted the model to ask again. *Fix:* on resume, run each approved call directly using the arguments saved on the run (`_dispatch_approved`), then give the model the request and the real result for that turn (`_to_agno_messages`). The panel gets normal `tool_started` and `tool_ended` events.

**3. Deny lost its own audit record.** The saved transcript was missing the denial. The code that compares the new transcript with what is already stored counts rows, and an earlier fix had removed the system message from the list, shifting every index by one. *Fix:* keep the transcript exactly as stored, and add the assistant message that asked for the denied call before the "denied" tool row (`_denied_result`). A tool message must always follow the assistant message that requested it, or later replays are invalid.

**Known cosmetic issue.** An approved turn's saved transcript can contain the user message twice, because Agno's output echoes its input. It is harmless (identical text, counts stay consistent) and was left unfixed.

**Lesson.** Test pause and resume end to end with a real model, including deny and redirect, not just the pieces. Keep what you store byte-for-byte consistent with what you compare against later.

---

## Agno needs a real SDK per provider (2026-08-06)

**What happened.** The first live test with a Groq key failed with "`groq` not installed", although the provider had validated and saved fine.

**Why.** Each Agno provider class is a thin wrapper around that provider's own SDK, so supporting N providers meant installing N SDKs. Validation only checked the provider *name*, never imported anything, so the gap showed only on the first real call.

**What we tried.** Pointing the existing `openai` provider record at Groq's URL (misleading hidden state), adding a second provider record (impossible: names must be unique and the provider slug selected the class), and installing the SDK (against the goal of not needing an SDK per provider).

**What we did.** Let an `AI Model` carry its own `api_key` and `base_url` so an OpenAI-compatible endpoint works with only the `openai` SDK. This was then generalised: every chat model now goes through one OpenAI-compatible client ([ADR 0014](decisions/0014-openai-compatible-chat-transport.md)), and `AI Model.provider` is an optional link ([ADR 0013](decisions/0013-litellm-for-provider-ux-agno-still-executes.md)).

**Lesson.** "The framework supports provider X" and "the package for X is installed" are different facts. Validate by actually constructing the thing, or avoid the per-provider dependency altogether. A Frappe `Link` field is checked on save before your own validation runs, so a field type can be the real blocker, not your code.

---

## Records of "what changed" for this docs set

Earlier versions of this file contained long working logs of these three items, including a verified-live transcript of the Groq runs. The conclusions are above; the code and tests (`frappe_ai/tests/test_chat_route.py`, `test_openai_transport.py`) are the lasting record.
