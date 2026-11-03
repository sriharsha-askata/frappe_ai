# Live voice mode: status and what is left

Design: [ADR 0025](../decisions/0025-live-voice-mode.md). Turn-based voice: microphone in front of the normal chat,
speech behind it; approvals stay on screen.

## Done

| Phase | What | Where |
|---|---|---|
| 1. Live streaming | The service sends frames as the model produces them (they used to be held until the turn ended). The fallback model is tried only before the client received anything. | `service/routes/agno_events.py`, `chat.py`; `test_chat_route.py`; `live-streaming` scenario |
| 2. Backend | `transcribe_voice` (phrase to plain text), `speak_text` (sentence to mp3), `AI Settings.speech_model` / `speech_voice`, `bootstrap.composer.voice`, rate limits and size caps | `voice/`, `api/frontend.py`; `test_voice.py`; `voice-api` scenario |
| 3. Push-to-talk and spoken replies | Mic button in the composer. Click to listen, click to send. The reply is spoken sentence by sentence while it streams (server voice, or the browser's voice when no speech model is set). Typing, stopping, or starting a chat silences it. | `frontend/src/lib/*`, `src/state/useVoice*.ts`, `src/components/VoiceButton.tsx` |

Phase 3 pieces: `wav.ts` (16-bit mono WAV, downsampling), `voiceCapture.ts` (microphone to WAV; a
`ScriptProcessorNode` collects 16 kHz audio), `sentences.ts` (streamed text to speakable sentences; markdown, links and
code blocks are never read aloud), `speechPlayer.ts` (ordered playback with one sentence of prefetch, browser-voice
fallback when a server request fails, `stop()`), `voiceReplies.ts` (run events to speech; tool activity is never spoken).
Frontend unit tests: `frontend/tests/*.test.ts` (35 tests, `npm test`).

## Not built yet

### Phase 4: hands-free

- **Voice activity detection** (`lib/vad.ts`): an energy detector with an adaptive noise floor and hysteresis. Speech starts
  above the floor, ends after about 800 ms of silence, ignores anything under 300 ms, caps a phrase at 30 s. A pure state
  machine, unit-tested with synthetic speech, silence and noise.
- **Auto turn-taking:** keep the microphone open in voice mode, record each detected phrase, send it, wait for the reply
  to finish speaking, listen again. Pre-roll of about 300 ms so the first word is not clipped. Move capture to an
  `AudioWorklet` (loaded from a Blob URL, with `ScriptProcessorNode` as the fallback) so it does not run on the main thread.
- **Barge-in:** speech onset while the app is speaking stops playback, calls `stopRun`, waits for the run to be marked
  Failed (`AISession.assert_not_blocked` refuses a new turn while a run is Running or Paused), then records the new phrase.
  Use a higher, sustained threshold during playback and an "allow interrupting" setting, because echo cancellation is
  unreliable on speakers; show a "use headphones to interrupt" hint.
- **Approvals:** when a run pauses for approval, speak that approval is needed and wait. Approval is by click only.
- Drop a transcript that finishes after the user typed or started a newer turn (already done for push-to-talk).

### Phase 5: real-browser end-to-end

`dev/e2e/browser_voice.py` drives Chromium with a fake microphone for the push-to-talk path. Extend it to: hands-free
turn-taking, barge-in (inject speech during playback, expect playback and the run to stop), an approval pause that is
announced but not approved by voice, and a denied microphone permission. Also run it against the Postgres site and with
the browser voice (no speech model).

## Known limits

- Each phrase and each spoken sentence is one request that holds a web worker for one to three seconds. Moving both to the
  asynchronous FastAPI service is the follow-up if concurrency needs it.
- Delay is speech-to-text plus the model's first words plus the first sentence's speech, typically one to four seconds.
- Real speech quality, latency and noisy rooms are untested here (stub speech services only), as are Safari, Firefox and
  mobile browsers. The microphone needs `https` or `localhost`.
- The recording is limited to 60 seconds and WAV at 16 kHz (about 32 KB per second) is sent as is.
