/** Cut streamed reply text into sentences that can be spoken as soon as they are complete. */

/** Sentences shorter than this are joined to the next one: many tiny requests sound choppy and cost more. */
const MIN_SENTENCE_CHARS = 24;
/** A sentence is never held longer than this; the speech service accepts at most 1,000 characters. */
const MAX_SENTENCE_CHARS = 400;
const CODE_FENCE = "```";
/** A full stop after these is not the end of a sentence. */
const ABBREVIATIONS = /\b(?:e\.g|i\.e|etc|vs|Dr|Mr|Mrs|Ms|St|No)\.$/i;
/** A terminator (with any closing quote or bracket) followed by whitespace ends a sentence. */
const SENTENCE_END = /[.!?…]+["'”’)\]]*\s+|\n+/g;

/** Remove the markup that would be read aloud as noise: emphasis, headings, bullets, links, URLs, code. */
export function cleanForSpeech(text: string): string {
	return text
		.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
		.replace(/https?:\/\/\S+/g, "link")
		.replace(/`([^`]*)`/g, "$1")
		.replace(/^\s{0,3}#{1,6}\s+/gm, "")
		.replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")
		.replace(/(\*\*|__|\*|_|~~)/g, "")
		.replace(/\|/g, " ")
		.replace(/\s+/g, " ")
		.trim();
}

export class SentenceSplitter {
	private buffer = "";
	private carry = "";

	/** Add streamed text; returns the sentences that are now complete, ready to speak. */
	push(delta: string): string[] {
		this.buffer += delta;
		return this.take(false);
	}

	/** The stream ended: return whatever is left, even if it has no final full stop. */
	flush(): string[] {
		return this.take(true);
	}

	reset(): void {
		this.buffer = "";
		this.carry = "";
	}

	private take(final: boolean): string[] {
		const sentences: string[] = [];
		const { speakable, held } = splitAtOpenFence(this.buffer, final);
		const { complete, rest } = cutSentences(speakable, final);
		this.buffer = rest + held;
		for (const raw of complete) {
			const sentence = this.join(cleanForSpeech(raw), final);
			if (sentence) sentences.push(sentence);
		}
		if (final && this.carry) {
			sentences.push(this.carry);
			this.carry = "";
		}
		return sentences;
	}

	/** Join a short sentence to the next one; returns the sentence to speak now, or "" to keep waiting. */
	private join(sentence: string, final: boolean): string {
		if (!/[\p{L}\p{N}]/u.test(sentence)) return "";
		const combined = this.carry ? `${this.carry} ${sentence}` : sentence;
		if (combined.length < MIN_SENTENCE_CHARS && !final) {
			this.carry = combined;
			return "";
		}
		this.carry = "";
		return combined;
	}
}

/** Remove finished code blocks; hold back an unfinished one (and a possible start of one) until it closes. */
function splitAtOpenFence(buffer: string, final: boolean): { speakable: string; held: string } {
	let text = buffer;
	for (let open = text.indexOf(CODE_FENCE); open !== -1; open = text.indexOf(CODE_FENCE)) {
		const close = text.indexOf(CODE_FENCE, open + CODE_FENCE.length);
		if (close === -1) {
			return final ? { speakable: text.slice(0, open), held: "" } : { speakable: text.slice(0, open), held: text.slice(open) };
		}
		text = `${text.slice(0, open)} ${text.slice(close + CODE_FENCE.length)}`;
	}
	const trailingTicks = final ? 0 : /`{1,2}$/.exec(text)?.[0].length ?? 0;
	return { speakable: text.slice(0, text.length - trailingTicks), held: text.slice(text.length - trailingTicks) };
}

/** Split `text` after each sentence end; `rest` is the unfinished tail (all of it when `final`). */
function cutSentences(text: string, final: boolean): { complete: string[]; rest: string } {
	const complete: string[] = [];
	let start = 0;
	for (const match of text.matchAll(SENTENCE_END)) {
		const end = (match.index ?? 0) + match[0].length;
		const sentence = text.slice(start, end);
		if (ABBREVIATIONS.test(sentence.trimEnd())) continue;
		complete.push(sentence);
		start = end;
	}
	let rest = text.slice(start);
	while (rest.length > MAX_SENTENCE_CHARS) {
		const cut = breakPoint(rest);
		complete.push(rest.slice(0, cut));
		rest = rest.slice(cut);
	}
	if (final && rest.trim()) {
		complete.push(rest);
		rest = "";
	}
	return { complete, rest };
}

/** Where to cut an over-long sentence: after the last comma or space within the limit. */
function breakPoint(text: string): number {
	const window = text.slice(0, MAX_SENTENCE_CHARS);
	const comma = Math.max(window.lastIndexOf(", "), window.lastIndexOf("; "));
	const space = window.lastIndexOf(" ");
	return (comma > 0 ? comma + 1 : space > 0 ? space : MAX_SENTENCE_CHARS) + 1;
}
