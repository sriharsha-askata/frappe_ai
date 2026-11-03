import assert from "node:assert/strict";
import test from "node:test";
import { bucketLabel, groupSessions } from "../src/lib/sessionGroups";
import type { SessionRecord } from "../src/state/types";

const NOW = new Date(2026, 10, 10, 15, 0, 0);

function chat(name: string, daysAgo: number, pinned = false): SessionRecord {
	const modified = new Date(2026, 10, 10 - daysAgo, 9, 0, 0).toISOString();
	return { id: name, name, title: name, preview: name, modified, pinned };
}

test("chats are bucketed by calendar day, not by 24-hour windows", () => {
	assert.equal(bucketLabel(chat("a", 0).modified, NOW), "Today");
	assert.equal(bucketLabel(chat("a", 1).modified, NOW), "Yesterday");
	assert.equal(bucketLabel(chat("a", 7).modified, NOW), "Previous 7 days");
	assert.equal(bucketLabel(chat("a", 8).modified, NOW), "Older");
});

test("a missing or invalid date falls in Older", () => {
	assert.equal(bucketLabel(undefined, NOW), "Older");
	assert.equal(bucketLabel("not a date", NOW), "Older");
});

test("pinned chats form the first group and are left out of the dated groups", () => {
	const groups = groupSessions([chat("old pinned", 30, true), chat("today", 0), chat("last week", 3)], NOW);
	assert.deepEqual(
		groups.map(([label, rows]) => [label, rows.map((row) => row.name)]),
		[
			["Pinned", ["old pinned"]],
			["Today", ["today"]],
			["Previous 7 days", ["last week"]],
		]
	);
});

test("without pinned chats there is no Pinned group", () => {
	assert.deepEqual(groupSessions([chat("today", 0)], NOW).map(([label]) => label), ["Today"]);
});
