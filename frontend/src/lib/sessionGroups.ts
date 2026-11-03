import type { SessionRecord } from "../state/types";
import { __ } from "./translate";

const DAY_MS = 86400000;

/** Date bucket for a chat: Today, Yesterday, Previous 7 days, or Older. */
export function bucketLabel(value?: string, now: Date = new Date()): string {
	if (!value) return __("Older");
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return __("Older");
	const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
	const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
	const age = Math.round((today.getTime() - day.getTime()) / DAY_MS);
	if (age <= 0) return __("Today");
	if (age === 1) return __("Yesterday");
	if (age <= 7) return __("Previous 7 days");
	return __("Older");
}

/** Pinned chats first, then the rest grouped by date in the order they arrive (newest first). */
export function groupSessions(items: SessionRecord[], now: Date = new Date()): [string, SessionRecord[]][] {
	const groups = new Map<string, SessionRecord[]>();
	for (const item of items) {
		if (item.pinned) continue;
		const label = bucketLabel(item.modified, now);
		groups.set(label, [...(groups.get(label) || []), item]);
	}
	const pinned = items.filter((item) => item.pinned);
	const output: [string, SessionRecord[]][] = pinned.length ? [[__("Pinned"), pinned]] : [];
	return output.concat(Array.from(groups.entries()));
}
