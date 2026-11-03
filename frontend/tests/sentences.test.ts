import assert from "node:assert/strict";
import test from "node:test";
import { SentenceSplitter, cleanForSpeech } from "../src/lib/sentences";

function speak(deltas: string[]): string[] {
	const splitter = new SentenceSplitter();
	const spoken: string[] = [];
	for (const delta of deltas) spoken.push(...splitter.push(delta));
	return [...spoken, ...splitter.flush()];
}

test("a sentence is released once the text after it shows it has ended", () => {
	const splitter = new SentenceSplitter();
	assert.deepEqual(splitter.push("The warehouse opens at nine in the morning."), [], "no whitespace yet: could be '9.5'");
	assert.deepEqual(splitter.push(" It closes"), ["The warehouse opens at nine in the morning."]);
	assert.deepEqual(splitter.flush(), ["It closes"]);
});

test("text split across arbitrary deltas gives the same sentences", () => {
	const text = "First we check the stock levels today. Then we reorder anything that is low! Finally we update the sheet.";
	const whole = speak([text]);
	const chopped = speak(text.match(/.{1,5}/g) as string[]);
	assert.deepEqual(chopped, whole);
	assert.equal(whole.length, 3);
});

test("a decimal number or an abbreviation does not end a sentence", () => {
	assert.deepEqual(speak(["The total is 3.5 units, e.g. boxes of ten. Next item please sir."]), [
		"The total is 3.5 units, e.g. boxes of ten.",
		"Next item please sir.",
	]);
});

test("very short sentences are joined to the next one", () => {
	assert.deepEqual(speak(["Yes. I found the invoice you asked about earlier."]), [
		"Yes. I found the invoice you asked about earlier.",
	]);
});

test("a paragraph break ends a sentence", () => {
	assert.deepEqual(speak(["Here is the summary of the report\n\nIt has two parts to read through."]), [
		"Here is the summary of the report",
		"It has two parts to read through.",
	]);
});

test("code blocks are never spoken, even when the fence arrives in pieces", () => {
	const spoken = speak(["Run this command first, please. ``", "`bash\nrm -rf /tmp/x\n``", "` After that, check the log output."]);
	assert.equal(spoken.join(" ").includes("rm -rf"), false);
	assert.ok(spoken.join(" ").includes("After that, check the log output."));
});

test("an unfinished code block at the end is dropped, not read out", () => {
	const spoken = speak(["Here is the script that does it. ```python\nprint('x')"]);
	assert.deepEqual(spoken, ["Here is the script that does it."]);
});

test("markdown markers, links, bullets and URLs are cleaned for speech", () => {
	assert.equal(cleanForSpeech("**Bold** and _italic_ with `code`"), "Bold and italic with code");
	assert.equal(cleanForSpeech("See [the guide](https://example.com/a) now"), "See the guide now");
	assert.equal(cleanForSpeech("Open https://example.com/very/long/path today"), "Open link today");
	assert.equal(cleanForSpeech("# Heading\n- first item\n2. second item"), "Heading first item second item");
});

test("text with nothing to say aloud produces nothing", () => {
	assert.deepEqual(speak(["---\n\n***\n"]), []);
});

test("a very long sentence is cut so it stays within the speech limit", () => {
	const long = Array.from({ length: 120 }, (_, i) => `word${i}`).join(" ");
	const spoken = speak([long]);
	assert.ok(spoken.length > 1);
	assert.ok(spoken.every((sentence) => sentence.length <= 401));
	assert.equal(spoken.join(" "), long);
});

test("reset forgets text that was held back", () => {
	const splitter = new SentenceSplitter();
	splitter.push("Half a thought");
	splitter.reset();
	assert.deepEqual(splitter.flush(), []);
});
