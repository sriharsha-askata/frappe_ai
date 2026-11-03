/** Speak sentences one after another, from the server's voice or the browser's own. */

/** How many sentences are requested from the server ahead of the one being played. Kept low because
 * each request holds a web worker for a second or two. */
const PREFETCH_AHEAD = 1;
/** A silent clip played inside a click, which is what allows later playback without another gesture. */
const SILENT_CLIP = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

export type SpeechClip = { audio: string; format: string };

/** The few parts of `HTMLAudioElement` the player uses, so tests can supply a fake. */
export type AudioLike = {
	play(): Promise<void>;
	pause(): void;
	onended: ((event: Event) => unknown) | null;
	onerror: ((event: Event | string) => unknown) | null;
};

/** The few parts of `speechSynthesis` the player uses. */
export type SynthLike = {
	speak(utterance: any): void;
	cancel(): void;
};

export type SpeechPlayerOptions = {
	/** "server" asks `fetchSpeech` for audio; "browser" uses the browser's own voice. */
	tts: "server" | "browser";
	fetchSpeech: (text: string) => Promise<SpeechClip>;
	onSpeakingChange?: (speaking: boolean) => void;
	createAudio?: (src: string) => AudioLike;
	createUtterance?: (text: string) => any;
	synth?: SynthLike | null;
};

type QueueItem = { text: string; clip?: Promise<SpeechClip | null> };

export class SpeechPlayer {
	private queue: QueueItem[] = [];
	private playing = false;
	/** Bumped by `stop()`; a sentence that finishes after that must not carry on with the queue. */
	private generation = 0;
	private current: { stop: () => void } | null = null;
	private readonly options: SpeechPlayerOptions;

	constructor(options: SpeechPlayerOptions) {
		this.options = options;
	}

	get speaking(): boolean {
		return this.playing || this.queue.length > 0;
	}

	/** Call inside a click: browsers only allow audio that was started, or unlocked, by a user gesture. */
	unlock(): void {
		try {
			void this.makeAudio(SILENT_CLIP).play().catch(() => undefined);
			this.synth()?.speak(this.makeUtterance(""));
		} catch {
			// Unlocking is best effort; playback will report its own failure.
		}
	}

	enqueue(text: string): void {
		this.queue.push({ text });
		this.prefetch();
		if (!this.playing) void this.run(this.generation);
	}

	/** Stop speaking now and forget everything still waiting. */
	stop(): void {
		this.generation += 1;
		this.queue = [];
		this.current?.stop();
		this.current = null;
		this.synth()?.cancel();
		this.setPlaying(false);
	}

	private async run(generation: number): Promise<void> {
		this.setPlaying(true);
		while (generation === this.generation && this.queue.length > 0) {
			const item = this.queue[0];
			await this.speak(item);
			if (generation !== this.generation) return;
			this.queue.shift();
			this.prefetch();
		}
		if (generation === this.generation) this.setPlaying(false);
	}

	private async speak(item: QueueItem): Promise<void> {
		const clip = this.options.tts === "server" ? await this.clipFor(item) : null;
		if (clip) {
			await this.playClip(clip);
		} else {
			await this.speakInBrowser(item.text);
		}
	}

	/** Server audio for a sentence; null when the request failed, so the browser voice covers for it. */
	private clipFor(item: QueueItem): Promise<SpeechClip | null> {
		item.clip ??= this.options.fetchSpeech(item.text).catch(() => null);
		return item.clip;
	}

	private prefetch(): void {
		if (this.options.tts !== "server") return;
		this.queue.slice(0, PREFETCH_AHEAD + 1).forEach((item) => void this.clipFor(item));
	}

	private playClip(clip: SpeechClip): Promise<void> {
		return new Promise((resolve) => {
			const audio = this.makeAudio(`data:audio/${clip.format === "mp3" ? "mpeg" : clip.format};base64,${clip.audio}`);
			const finish = () => {
				this.current = null;
				resolve();
			};
			audio.onended = finish;
			audio.onerror = finish;
			this.current = { stop: () => { audio.pause(); finish(); } };
			audio.play().catch(finish);
		});
	}

	private speakInBrowser(text: string): Promise<void> {
		const synth = this.synth();
		if (!synth) return Promise.resolve();
		return new Promise((resolve) => {
			const utterance = this.makeUtterance(text);
			const finish = () => {
				this.current = null;
				resolve();
			};
			utterance.onend = finish;
			utterance.onerror = finish;
			this.current = { stop: finish };
			synth.speak(utterance);
		});
	}

	private setPlaying(playing: boolean): void {
		if (this.playing === playing) return;
		this.playing = playing;
		this.options.onSpeakingChange?.(playing);
	}

	private makeAudio(src: string): AudioLike {
		return this.options.createAudio ? this.options.createAudio(src) : new Audio(src);
	}

	private makeUtterance(text: string): any {
		return this.options.createUtterance ? this.options.createUtterance(text) : new SpeechSynthesisUtterance(text);
	}

	private synth(): SynthLike | null {
		if (this.options.synth !== undefined) return this.options.synth;
		return typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null;
	}
}
