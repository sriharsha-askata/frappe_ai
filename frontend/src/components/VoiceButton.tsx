import React, { useCallback, useRef } from "react";
import { Loader2, Mic, Square, Volume2 } from "lucide-react";
import { VoiceRecorder } from "../lib/voiceCapture";
import { __ } from "../lib/translate";
import { useStore } from "../state/store";
import { useVoiceRecorder } from "../state/useVoiceRecorder";

/** The microphone button of the composer: talk to the agent and hear the answer. */
export function VoiceButton() {
	const { voice, loaded, sending, paused, needsSetup, send, speaking, stopSpeaking, unlockAudio } = useStore();

	const ready = loaded && !sending && !paused && !needsSetup;
	const readyRef = useRef(ready);
	readyRef.current = ready;
	const canSend = useCallback(() => readyRef.current, []);

	const { status, error, toggle } = useVoiceRecorder({
		send,
		canSend,
		prepareAudio: unlockAudio,
		stopSpeaking,
	});

	if (!voice.available || !VoiceRecorder.supported()) return null;

	const recording = status === "recording";
	const transcribing = status === "transcribing";
	const speakingNow = speaking && status === "idle";
	const label = recording
		? __("Listening… click to send")
		: transcribing
		? __("Understanding…")
		: speakingNow
		? __("Speaking… click to stop")
		: __("Talk");

	return (
		<>
			<button
				type="button"
				className={`faip-icon-button faip-voice-button${recording ? " is-recording" : ""}`}
				aria-label={label}
				title={label}
				disabled={transcribing || (!ready && !recording && !speakingNow)}
				onClick={speakingNow ? stopSpeaking : toggle}
			>
				{transcribing ? <Loader2 size={15} className="faip-spin" /> : recording ? <Square size={15} /> : speakingNow ? <Volume2 size={15} /> : <Mic size={15} />}
			</button>
			{(recording || transcribing || speakingNow || error) && (
				<span className={`faip-voice-status${error && status === "idle" ? " is-error" : ""}`} role="status">
					{error && status === "idle" ? error : label}
				</span>
			)}
		</>
	);
}
