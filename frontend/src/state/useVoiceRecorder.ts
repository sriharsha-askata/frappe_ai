import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api/client";
import { __ } from "../lib/translate";
import { MAX_RECORDING_SECONDS, VoiceRecorder } from "../lib/voiceCapture";

export type RecorderStatus = "idle" | "recording" | "transcribing";

type Options = {
	/** Send the recognized text as a spoken request. */
	send: (text: string, options: { spoken: true }) => Promise<void>;
	/** Whether a new request may start now (nothing running, nothing waiting for approval). */
	canSend: () => boolean;
	/** Called inside the click that starts recording, so later playback is allowed. */
	prepareAudio: () => void;
	/** Silence the reply that is still being spoken. */
	stopSpeaking: () => void;
};

/** Push-to-talk: click to start listening, click again to send what was said. */
export function useVoiceRecorder({ send, canSend, prepareAudio, stopSpeaking }: Options) {
	const [status, setStatus] = useState<RecorderStatus>("idle");
	const [error, setError] = useState("");
	const recorder = useRef<VoiceRecorder | null>(null);
	const limitTimer = useRef<number | undefined>(undefined);

	const finish = useCallback(async () => {
		window.clearTimeout(limitTimer.current);
		setStatus("transcribing");
		try {
			const wav = await recorder.current?.stop();
			recorder.current = null;
			const text = wav ? await api.transcribeVoice(wav) : "";
			setStatus("idle");
			if (!wav) return setError(__("That was too short. Speak a little longer."));
			if (!text) return setError(__("I didn't catch that. Try again."));
			if (canSend()) void send(text, { spoken: true }); // dropped if the user typed or started something meanwhile
		} catch (failure: any) {
			setStatus("idle");
			setError(failure?.message || __("Could not understand the recording."));
		}
	}, [canSend, send]);

	const toggle = useCallback(async () => {
		if (status === "recording") return finish();
		if (status !== "idle") return;
		setError("");
		stopSpeaking();
		prepareAudio();
		try {
			recorder.current = new VoiceRecorder();
			await recorder.current.start();
			setStatus("recording");
			limitTimer.current = window.setTimeout(() => void finish(), MAX_RECORDING_SECONDS * 1000);
		} catch (failure) {
			recorder.current = null;
			setError(microphoneError(failure));
		}
	}, [finish, prepareAudio, status, stopSpeaking]);

	useEffect(
		() => () => {
			window.clearTimeout(limitTimer.current);
			recorder.current?.cancel();
		},
		[]
	);

	return { status, error, toggle };
}

function microphoneError(failure: unknown): string {
	const name = (failure as { name?: string })?.name;
	if (name === "NotAllowedError") return __("Microphone access was blocked. Allow it in your browser's site settings.");
	if (name === "NotFoundError") return __("No microphone was found.");
	return __("Could not start the microphone.");
}
