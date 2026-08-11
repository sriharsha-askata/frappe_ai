import { __ } from "../lib/translate";

type RequestOptions = {
	method?: 'GET' | 'POST';
	params?: Record<string, any>;
	body?: Record<string, any> | FormData;
};

function buildUrl(methodPath: string, params?: Record<string, any>) {
	const url = new URL(`/api/method/${methodPath}`, window.location.origin);
	for (const [key, value] of Object.entries(params || {})) {
		if (value === undefined || value === null || value === "") continue;
		if (Array.isArray(value)) {
			url.searchParams.set(key, JSON.stringify(value));
		} else {
			url.searchParams.set(key, String(value));
		}
	}
	return url.toString();
}

async function request(methodPath: string, options: RequestOptions = {}) {
	const method = options.method || "GET";
	const init: RequestInit = { method, credentials: "same-origin" };
	let url = buildUrl(methodPath, method === "GET" ? options.params : undefined);

	if (method !== "GET") {
		init.headers = { "X-Frappe-CSRF-Token": frappe.csrf_token };
		if (options.body instanceof FormData) {
			init.body = options.body;
		} else {
			init.headers = {
				...(init.headers || {}),
				"Content-Type": "application/json",
			};
			init.body = JSON.stringify(options.body || options.params || {});
		}
	} else if (options.body && !(options.body instanceof FormData)) {
		url = buildUrl(methodPath, options.body);
	}

	const response = await fetch(url, init);
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(serverMessage(data) || __("Request failed ({0})", [response.status]));
	}
	if (data.exc) {
		throw new Error(serverMessage(data) || __("Request failed."));
	}
	return data.message ?? data;
}

export const bootstrap = () => request("frappe_ai.api.frontend.bootstrap");

export const loadHistory = () =>
	request("frappe_ai.api.frontend.sessions").then((data) => data.sessions || []);

export const searchSessions = (query: string) =>
	request("frappe_ai.api.frontend.sessions", { params: { query, limit: 20 } }).then(
		(data) => data.sessions || []
	);

export const getSession = (session: string) =>
	request("frappe_ai.api.frontend.session_detail", { params: { session } });

export const submitFeedback = (args: Record<string, any>) =>
	request("frappe_ai.api.frontend.submit_feedback", {
		method: "POST",
		body: {
			run: args.run_name,
			rating: args.rating,
			comment: args.comment,
		},
	});

export const recoverSession = (session: string) =>
	request("frappe_ai.api.frontend.recover_session", { method: "POST", body: { session } });

export const stopRun = (runName: string) =>
	request("frappe_ai.api.frontend.stop_run", { method: "POST", body: { run: runName } });

export const getAgentTools = (agent: string) =>
	request("frappe_ai.api.frontend.agent_tools", { params: { agent } }).then((data) => {
		const tools = data.tools || {};
		return Object.fromEntries(
			Object.entries(tools).map(([slug, meta]: any) => [slug, Boolean(meta?.requires_confirmation)])
		);
	});

export async function uploadAttachment(file: File) {
	const form = new FormData();
	form.append("file", file, file.name);
	return request("frappe_ai.api.frontend.upload_attachment", {
		method: "POST",
		body: form,
	}).then((data) => data.attachment);
}

export function serverMessage(data: any) {
	try {
		const messages = JSON.parse(data._server_messages || "[]");
		if (messages.length) return JSON.parse(messages[0]).message;
	} catch {
		// ignore
	}
	return data.exception || data._error_message || data.message || null;
}
