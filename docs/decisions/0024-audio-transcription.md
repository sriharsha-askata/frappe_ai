# ADR 0024 — Audio by transcription

**Status:** Accepted
**Date:** 2026-11-04
**Deciders:** Sri Harsha Dabbiru

---

## Context

Uploads were converted to text on the Frappe side and only that text reached the model, so audio
files were rejected. Letting the model hear audio needs a model that accepts it plus changes to how
the service builds and replays messages (ADR 0014). Transcribing first needs neither.

## Decision

Audio files are transcribed to text on upload, and the transcript flows through the existing
attachment, retrieval and knowledge paths.

1. **Any OpenAI-compatible `/audio/transcriptions` endpoint** does the work (OpenAI, Groq, a local
   faster-whisper server, LocalAI, vLLM). It is reached with the shared OpenAI SDK client and the
   credentials of the model's `AI Provider`. Ollama does not serve this endpoint.
2. **Off until configured.** `AI Settings.transcription_model` names the model; empty means audio is
   refused with "Audio needs a transcription model". The field states that audio is sent to that
   provider. `max_audio_mb` (25, the OpenAI limit) and `transcription_timeout` (110 s, under the usual
   120 s web timeout) bound the request.
3. **One choke point.** `extract_file` (used by chat attachments and knowledge File sources) hands
   audio to `knowledge/transcribe.py`. `supported_extensions()` offers audio types in the composer only
   when a model is configured.
4. **Transcript format.** One `[hh:mm:ss] text` line per segment from `verbose_json`, so retrieval chunks
   keep their position in the recording; plain text when the provider sends no segments.
5. **Safe by construction.** The size limit is checked from the file's recorded size before it is read or
   sent. An expired audio upload asks the user to attach again instead of silently transcribing a
   second time. Provider errors go through `normalize_provider_error`, and API keys a provider echoes in
   an error message are masked (`redact_secrets`).
6. **Transcripts are untrusted text**, handled like any document: they enter the prompt as attachment
   content and are never routed into tool arguments.

## Consequences

**Positive:** audio works in chat and in knowledge bases with no change to the agent loop, the service
or the frontend; any compatible provider works; nothing happens unless an admin opts in.
**Negative:** the model understands what was said but not tone, speakers or sound; upload is
synchronous, so a long transcription holds a web worker up to the timeout, which is why files over the
limit are refused rather than split.

## Not included (follow-ups)

Native audio input to the model; speaker diarization; asynchronous transcription with a status and ffmpeg
chunking for files over the limit; a transcription check in the AI Model connection test.

## Verification

`frappe_ai/tests/test_transcribe.py` (19 tests) and the `audio` scenario of the end-to-end harness, which
uploads real WAV files through the HTTP API on MariaDB and Postgres against a stub transcriber. A real
provider's transcription quality is not tested.
