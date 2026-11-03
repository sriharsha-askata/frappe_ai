import assert from "node:assert/strict";
import test from "node:test";
import { SpeechPlayer } from "../src/lib/speechPlayer";
import type { AudioLike, SpeechClip } from "../src/lib/speechPlayer";

class FakeAudio implements AudioLike {
	onended: (() => void) | null = null;
	onerror: (() => void) | null = null;
	paused = false;
	constructor(readonly src: string, private readonly log: string[]) {}
	play(): Promise<void> {
		this.log.push(`play ${this.src.slice(0, 22)}`);
		return Promise.resolve();
	}
	pause(): void {
		this.paused = true;
	}
}

class Harness {
	log: string[] = [];
	requested: string[] = [];
	audios: FakeAudio[] = [];
	utterances: any[] = [];
	cancelled = 0;
	speakingChanges: boolean[] = [];
	failFor = new Set<string>();

	player(tts: "server" | "browser" = "server") {
		return new SpeechPlayer({
			tts,
			fetchSpeech: async (text: string): Promise<SpeechClip> => {
				this.requested.push(text);
				if (this.failFor.has(text)) throw new Error("tts down");
				return { audio: Buffer.from(text).toString("base64"), format: "mp3" };
			},
			createAudio: (src) => {
				const audio = new FakeAudio(src, this.log);
				this.audios.push(audio);
				return audio;
			},
			createUtterance: (text) => {
				const utterance: any = { text, onend: null, onerror: null };
				this.utterances.push(utterance);
				return utterance;
			},
			synth: { speak: (u) => this.log.push(`say ${u.text}`), cancel: () => { this.cancelled += 1; } },
			onSpeakingChange: (speaking) => this.speakingChanges.push(speaking),
		});
	}
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const finishPlaying = async (audio: FakeAudio) => {
	audio.onended?.();
	await tick();
};

test("sentences are spoken in order, one at a time", async () => {
	const h = new Harness();
	const player = h.player();
	player.enqueue("First.");
	player.enqueue("Second.");
	await tick();
	assert.equal(h.audios.length, 1, "only the first sentence plays");
	await finishPlaying(h.audios[0]);
	assert.equal(h.audios.length, 2);
	await finishPlaying(h.audios[1]);
	assert.equal(player.speaking, false);
	assert.deepEqual(h.speakingChanges, [true, false]);
});

test("the next sentence is requested while the current one plays, but no further ahead", async () => {
	const h = new Harness();
	const player = h.player();
	["One.", "Two.", "Three.", "Four."].forEach((s) => player.enqueue(s));
	await tick();
	assert.deepEqual(h.requested, ["One.", "Two."]);
	await finishPlaying(h.audios[0]);
	assert.deepEqual(h.requested, ["One.", "Two.", "Three."]);
});

test("a sentence the server fails to speak is read by the browser voice instead", async () => {
	const h = new Harness();
	h.failFor.add("Broken.");
	const player = h.player();
	player.enqueue("Broken.");
	await tick();
	assert.ok(h.log.includes("say Broken."));
	h.utterances[0].onend();
	await tick();
	assert.equal(player.speaking, false);
});

test("browser voice speaks directly and asks the server for nothing", async () => {
	const h = new Harness();
	const player = h.player("browser");
	player.enqueue("Hello there.");
	await tick();
	assert.deepEqual(h.requested, []);
	assert.ok(h.log.includes("say Hello there."));
	h.utterances[0].onend();
	await tick();
	assert.equal(player.speaking, false);
});

test("stop cancels playback and drops what is waiting", async () => {
	const h = new Harness();
	const player = h.player();
	player.enqueue("Playing.");
	player.enqueue("Waiting.");
	await tick();
	player.stop();
	await tick();
	assert.equal(h.audios[0].paused, true);
	assert.equal(h.cancelled, 1);
	assert.equal(player.speaking, false);
	assert.equal(h.audios.length, 1, "the waiting sentence never starts");
});

test("a sentence added after stop is spoken normally", async () => {
	const h = new Harness();
	const player = h.player();
	player.enqueue("Old.");
	await tick();
	player.stop();
	player.enqueue("New.");
	await tick();
	assert.equal(h.audios.length, 2);
	assert.ok(h.audios[1].src.includes(Buffer.from("New.").toString("base64")));
});

test("unlock plays a silent clip so later playback is allowed", () => {
	const h = new Harness();
	h.player().unlock();
	assert.equal(h.audios.length, 1);
	assert.ok(h.log[0].startsWith("play data:audio/wav"));
});
