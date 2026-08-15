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

export function parseToolIdentity(name: string) {
	const raw = String(name || "").trim();
	if (!raw.includes("<|")) {
		return { kind: "tool" as const, toolName: raw, connectionName: null };
	}
	const [toolName, connectionName] = raw.split("<|", 2);
	return {
		kind: "mcp_tool" as const,
		toolName: toolName.trim(),
		connectionName: connectionName?.trim() || null,
	};
}

export function toolLabel(name: string) {
	return LABELS[name] ? __(LABELS[name]) : humanize(name);
}

export function summarizeValues(values: Record<string, any>) {
	return Object.entries(values || {})
		.slice(0, 4)
		.map(([label, value]) => ({
			label,
			value:
				typeof value === "string"
					? value
					: Array.isArray(value)
						? value.join(", ")
						: value && typeof value === "object"
							? Object.entries(value)
									.slice(0, 2)
									.map(([key, item]) => `${key}: ${String(item)}`)
									.join(", ")
							: String(value),
		}))
		.filter((row) => row.value && row.value !== "undefined");
}

export function resultSummary(result: string | null) {
	if (!result) return "";
	try {
		const parsed = JSON.parse(result);
		if (parsed && typeof parsed === "object") {
			for (const key of ["message", "result", "status", "error"]) {
				if (typeof parsed[key] === "string" && parsed[key]) return parsed[key];
			}
			return JSON.stringify(parsed, null, 2);
		}
		return String(parsed);
	} catch {
		return result;
	}
}

export function executionStatusFromResult(result: string | null) {
	if (!result) return { status: "running", error: null, approvalStatus: null };
	try {
		const parsed = JSON.parse(result);
		if (parsed && typeof parsed === "object") {
			const status = parsed.error ? "error" : "completed";
			const approvalStatus =
				parsed.status === "approved"
					? "approved"
					: parsed.status === "denied"
						? "denied"
						: parsed.status === "redirect"
							? "redirected"
							: null;
			return { status, error: parsed.error || null, approvalStatus };
		}
	} catch {
		// ignore
	}
	return { status: "completed", error: null, approvalStatus: null };
}

export function summarizeSchema(schema: any) {
	const properties = schema?.properties || {};
	return Object.entries(properties)
		.slice(0, 6)
		.map(([name, field]: any) => ({
			name,
			type: field?.type || field?.anyOf?.map((item: any) => item.type).filter(Boolean).join(" | ") || "value",
			description: field?.description || "",
		}));
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
			(typeof parsed.description === "string" && parsed.description.trim()) || __("Run Python code");
	}
	return { title: title || toolLabel(name), danger: name === "delete" };
}
