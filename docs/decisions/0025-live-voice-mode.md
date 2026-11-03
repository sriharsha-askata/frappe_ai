# ADR 0025 — Live voice mode

**Status:** Accepted
**Date:** 2026-11-04
**Deciders:** Sri Harsha Dabbiru

---

## Context

Users want to talk to an agent and hear it answer. A realtime speech-to-speech model would bypass the
agent loop, so tool approvals, per-user permissions and budgets would not apply. Turn-based voice keeps
all of that: it adds a microphone in front of the normal chat and a speaker behind it.

## Decision

Voice mode is **turn-based**:

1. **Listen.** The browser captures the microphone, detects the end of a phrase, and sends that phrase
   as audio to `frappe_ai.api.frontend.transcribe_voice`. The returned text goes through the normal
   `send()` path, so a spoken request is a typed request.
2. **Answer live.** The service streams reply text as the model produces it (see below). The browser
   cuts it into sentences and speaks each as it completes. The user can interrupt (barge-in).
3. **Speech out.** With `AI Settings.speech_model` set, each sentence is synthesized by an
   OpenAI-compatible `/audio/speech` endpoint (`frontend.speak_text`, base64 mp3). With none set, the
   browser's own speech synthesis speaks it, so voice mode works with no extra provider.
4. **Needs speech-to-text.** Voice is available only when `AI Settings.transcription_model` is set
   (ADR 0024). `bootstrap` reports `composer.voice = {available, tts}`.
5. **Approvals stay on screen.** A run that needs approval is announced and waits for a click. A spoken
   "yes" never approves a tool call: a misheard word or background speech must not authorise a write.
6. **Nothing is stored.** Utterance audio is transcribed and discarded; spoken replies are not kept.
   Transcripts are untrusted text, like typed input.

### Live streaming (prerequisite)

The service used to hold a whole turn and send every frame at the end, so nothing could be spoken early.
Frames now leave as Agno produces them. The fallback-model retry is allowed only while the client has
received nothing; after the first frame a retry would repeat output and could re-run a tool, so the run
fails instead. This helps every client, not only voice.

## Limits and trade-offs

- `transcribe_voice` and `speak_text` run in a Frappe web worker for one to three seconds each, with
  per-user rate limits (30 phrases and 90 sentences a minute) and size caps (5 MB audio, 1,000 characters).
  Prefetch is limited to one sentence ahead so one user cannot hold many workers. Moving both to the
  asynchronous FastAPI service is the follow-up if concurrency needs it.
- Typical delay is one to four seconds: speech-to-text, the model's first words, then the first sentence's
  speech. Echo without headphones can trigger false interruptions, so interrupting is a setting.
- The voice-activity detector is energy based; very noisy rooms need push-to-talk.

## Not included

Speech-to-speech realtime models, streaming partial transcripts, a wake word, speaker identification, voice
approvals, a language picker (the provider detects the language).

## Verification

`frappe_ai/tests/test_chat_route.py` (streaming order, fallback only before output, disconnect),
`frappe_ai/tests/test_voice.py`, the `live-streaming` and `voice-api` end-to-end scenarios on MariaDB and
Postgres, and the real-browser voice test described in `docs/progress/e2e-validation.md`.
