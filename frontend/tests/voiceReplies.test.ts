import assert from "node:assert/strict";
import test from "node:test";
import { VoiceReplies } from "../src/lib/voiceReplies";

function replies() {
	const spoken: string[] = [];
	const voice = new VoiceReplies({
		tts: "browser",
		fetchSpeech: async () => ({ audio: "", format: "mp3" }),
		createUtterance: (text) => ({ text, onend: null, onerror: null }),
		synth: {
			speak: (utterance: any) => {
				spoken.push(utterance.text);
				queueMicrotask(() => utterance.onend?.());
			},
			cancel: () => undefined,
		},
	});
	return { voice, spoken };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

test("a reply is spoken sentence by sentence as the text arrives", async () => {
	const { voice, spoken } = replies();
	voice.begin();
	voice.onEvent({ type: "text", delta: "The order shipped on Monday. " });
	voice.onEvent({ type: "text", delta: "It should arrive on Thursday afternoon" });
	await tick();
	assert.deepEqual(spoken, ["The order shipped on Monday."], "the unfinished sentence waits");
	voice.onEvent({ type: "done", status: "Completed" });
	await tick();
	assert.deepEqual(spoken, ["The order shipped on Monday.", "It should arrive on Thursday afternoon"]);
});

test("tool activity is never spoken", async () => {
	const { voice, spoken } = replies();
	voice.begin();
	voice.onEvent({ type: "tool_started", id: "1", name: "search", arguments: {} });
	voice.onEvent({ type: "tool_ended", id: "1", name: "search", result: "secret internal result" });
	voice.onEvent({ type: "done", status: "Completed" });
	await tick();
	assert.deepEqual(spoken, []);
});

test("a run that was not started by voice is not spoken", async () => {
	const { voice, spoken } = replies();
	voice.onEvent({ type: "text", delta: "Typed question, typed answer for you. " });
	voice.onEvent({ type: "done", status: "Completed" });
	await tick();
	assert.deepEqual(spoken, []);
});

test("an error stops speaking and ignores the rest of the run", async () => {
	const { voice, spoken } = replies();
	voice.begin();
	voice.onEvent({ type: "error", message: "provider failed" });
	voice.onEvent({ type: "text", delta: "Late text that must stay silent. " });
	await tick();
	assert.deepEqual(spoken, []);
});

test("starting a new spoken run discards text left over from the last one", async () => {
	const { voice, spoken } = replies();
	voice.begin();
	voice.onEvent({ type: "text", delta: "Half of an old answer" });
	voice.begin();
	voice.onEvent({ type: "text", delta: "A brand new answer is here. " });
	voice.onEvent({ type: "done", status: "Completed" });
	await tick();
	assert.deepEqual(spoken, ["A brand new answer is here."]);
});
