/** Speak a reply while it streams: text events become sentences, sentences become speech. */

import type { RunEvent } from "../api/stream";
import { SentenceSplitter } from "./sentences";
import { SpeechPlayer } from "./speechPlayer";
import type { SpeechPlayerOptions } from "./speechPlayer";

export class VoiceReplies {
	private readonly splitter = new SentenceSplitter();
	private readonly player: SpeechPlayer;
	private active = false;

	constructor(options: SpeechPlayerOptions) {
		this.player = new SpeechPlayer(options);
	}

	get speaking(): boolean {
		return this.player.speaking;
	}

	unlock(): void {
		this.player.unlock();
	}

	/** The next run was started by voice: speak its reply as it arrives. */
	begin(): void {
		this.stop();
		this.active = true;
	}

	/** Stop speaking and stop listening to the current run. */
	stop(): void {
		this.active = false;
		this.splitter.reset();
		this.player.stop();
	}

	/** Feed every run event here; only the assistant's text is spoken, never tool activity. */
	onEvent(event: RunEvent): void {
		if (!this.active) return;
		if (event.type === "text") {
			this.speakAll(this.splitter.push(event.delta || event.content || ""));
		} else if (event.type === "done") {
			this.speakAll(this.splitter.flush());
			this.active = false;
		} else if (event.type === "error") {
			this.stop();
		}
	}

	private speakAll(sentences: string[]): void {
		sentences.forEach((sentence) => this.player.enqueue(sentence));
	}
}
