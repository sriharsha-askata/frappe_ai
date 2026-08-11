import { __ } from "./translate";

export function parseArgs(args: unknown): Record<string, any> {
	if (!args) return {};
	if (typeof args === "object" && !Array.isArray(args)) return args as Record<string, any>;
	if (typeof args !== "string") return {};
	try {
		return JSON.parse(args);
	} catch {
		return {};
	}
}

export function rawArgs(args: unknown) {
	if (typeof args !== "string" || !args.trim()) return "";
	try {
		JSON.parse(args);
		return "";
	} catch {
		return args;
	}
}

export function humanize(name: string) {
	return String(name || "")
		.replace(/_/g, " ")
		.replace(/^./, (char) => char.toUpperCase());
}

const LABELS: Record<string, string> = {
	find_doctypes: "Finding relevant DocTypes",
	describe: "Reading DocType Meta",
	read: "Reading DocType Records",
	search_knowledge: "Searching Knowledge",
	execute: "Executing",
	create: "Creating Records",
	update: "Updating Records",
	delete: "Deleting Records",
	run_action: "Running Document Actions",
};

export function normalizeToolName(name: string) {
	const clean = String(name || "")
		.split("<|")[0]
		.trim();
	return clean || String(name || "").trim();
}

export function toolLabel(name: string) {
	return LABELS[name] ? __(LABELS[name]) : humanize(name);
}

export function toolContext(args: unknown) {
	const parsed = parseArgs(args);
	for (const key of ["doctype", "search", "action"]) {
		const value = parsed[key];
		if (typeof value === "string" && value) return key === "action" ? humanize(value) : value;
	}
	return null;
}

export function toolError(result: string | null) {
	if (typeof result !== "string") return null;
	try {
		const parsed = JSON.parse(result);
		if (parsed && typeof parsed === "object") {
			if (typeof parsed.error === "string") return parsed.error;
			const failures = parsed.failures;
			if (Array.isArray(failures) && failures.length) {
				const combined = failures
					.map((item) => item?.error)
					.filter((item) => typeof item === "string")
					.join("\n");
				return combined || null;
			}
		}
	} catch {
		return null;
	}
	return null;
}

const pick = (one: string, many: string, count: number, doctype: string) =>
	count === 1 ? __(one, [doctype]) : __(many, [count, doctype]);

export function confirmTitle(name: string, args: unknown) {
	const parsed = parseArgs(args);
	const doctype = typeof parsed.doctype === "string" ? parsed.doctype : "";
	const count = (value: unknown) => (Array.isArray(value) && value.length ? value.length : 1);
	let title = "";
	if (name === "create" && doctype) {
		title = pick("Create 1 {0} record", "Create {0} {1} records", count(parsed.records), doctype);
	} else if (name === "update" && doctype) {
		title = pick("Update 1 {0} record", "Update {0} {1} records", count(parsed.names), doctype);
	} else if (name === "delete" && doctype) {
		title = pick("Delete 1 {0} record", "Delete {0} {1} records", count(parsed.names), doctype);
	} else if (name === "run_action" && parsed.action) {
		title = __('Run "{0}" on {1}', [
			humanize(parsed.action),
			doctype ? `${count(parsed.names)} ${doctype}` : __("records"),
		]);
	} else if (name === "execute") {
		title =
			(typeof parsed.description === "string" && parsed.description.trim()) ||
			__("Run Python code");
	}
	return { title: title || toolLabel(name), danger: name === "delete" };
}
