import assert from "node:assert/strict";
import test from "node:test";
import { applyRunEvent } from "../src/state/runEvents";
import type { StoreState } from "../src/state/types";

function state(): StoreState {
	return {
		agents: [], models: [], history: [], selectedAgent: null, selectedModel: null,
		currentSession: null, currentRun: null, attachments: [], supportedFileTypes: [],
		sending: true, loaded: true, scrollTick: 0, forceScroll: false, focusTick: 0,
		inspector: { open: false, mode: "agent", title: "", payload: null },
		transcript: [{ id: "m1", role: "assistant", run: null, content: "", executions: [], questions: [], feedback: null, pending: true }],
	};
}

function assistant(current: StoreState) {
	const message = current.transcript[0];
	assert.equal(message.role, "assistant");
	return message;
}

test("text deltas accumulate without mutating prior state", () => {
	const initial = state();
	const first = applyRunEvent(initial, { type: "text", delta: "Hello" }, "m1");
	const second = applyRunEvent(first, { type: "text", delta: " world" }, "m1");
	assert.equal(assistant(second).content, "Hello world");
	assert.equal(assistant(initial).content, "");
});

test("duplicate tool_started updates one execution", () => {
	const first = applyRunEvent(state(), { type: "tool_started", id: "t1", name: "read", arguments: { doctype: "Task" } }, "m1");
	const second = applyRunEvent(first, { type: "tool_started", id: "t1", name: "read", arguments: { doctype: "Note" } }, "m1");
	assert.equal(assistant(second).executions.length, 1);
	assert.deepEqual(assistant(second).executions[0].raw_input, { doctype: "Note" });
});

test("tool_ended error result updates execution", () => {
	const started = applyRunEvent(state(), { type: "tool_started", id: "t1", name: "read", arguments: {} }, "m1");
	const ended = applyRunEvent(started, { type: "tool_ended", id: "t1", name: "read", result: '{"error":"Denied","status":"denied"}' }, "m1");
	assert.equal(assistant(ended).executions[0].status, "error");
	assert.equal(assistant(ended).executions[0].error, "Denied");
	assert.equal(assistant(ended).executions[0].approval_status, "denied");
});

test("paused done exposes questions and marks matching execution", () => {
	const started = applyRunEvent(state(), { type: "run_started", name: "r1", session: "s1" }, "m1");
	const tool = applyRunEvent(started, { type: "tool_started", id: "t1", name: "delete", arguments: {} }, "m1");
	const done = applyRunEvent(tool, { type: "done", status: "Paused", questions: [{ key: "t1", name: "delete", prompt: "Approve?", _answer: "old" }] }, "m1");
	assert.equal(done.currentRun?.status, "Paused");
	assert.equal(assistant(done).pending, false);
	assert.equal(assistant(done).questions[0]._answer, undefined);
	assert.equal(assistant(done).executions[0].status, "awaiting_confirmation");
});

test("terminal error marks run and assistant failed", () => {
	const started = applyRunEvent(state(), { type: "run_started", name: "r1", session: "s1" }, "m1");
	const failed = applyRunEvent(started, { type: "error", message: "Provider unavailable" }, "m1");
	assert.equal(failed.currentRun?.status, "Failed");
	assert.equal(assistant(failed).pending, false);
	assert.equal(assistant(failed).error, "Provider unavailable");
});

test("unknown event and missing assistant return prior state", () => {
	const initial = state();
	assert.strictEqual(applyRunEvent(initial, { type: "unknown", original: { type: "future" } }, "m1"), initial);
	assert.strictEqual(applyRunEvent(initial, { type: "text", delta: "ignored" }, "missing"), initial);
});
