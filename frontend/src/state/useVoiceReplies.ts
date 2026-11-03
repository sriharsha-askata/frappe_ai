import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api/client";
import type { RunEvent } from "../api/stream";
import { VoiceReplies } from "../lib/voiceReplies";
import type { VoiceInfo } from "./types";

/** Speak the replies of runs that were started by voice, and report whether speech is playing. */
export function useVoiceReplies(tts: VoiceInfo["tts"]) {
	const [speaking, setSpeaking] = useState(false);
	const replies = useRef<VoiceReplies | null>(null);

	// A new player is needed when the voice source changes (for example after settings are saved).
	useEffect(() => {
		replies.current?.stop();
		replies.current = new VoiceReplies({ tts, fetchSpeech: api.speakText, onSpeakingChange: setSpeaking });
		return () => replies.current?.stop();
	}, [tts]);

	const begin = useCallback(() => replies.current?.begin(), []);
	const stop = useCallback(() => replies.current?.stop(), []);
	const unlock = useCallback(() => replies.current?.unlock(), []);
	const onEvent = useCallback((event: RunEvent) => replies.current?.onEvent(event), []);

	return { speaking, begin, stop, unlock, onEvent };
}
