import { serverMessage } from "./client";
import { __ } from "../lib/translate";

type EventHandler = (event: any) => void;

async function openRun(
	methodPath: string,
	body: Record<string, any>,
	streamBody: Record<string, any>,
	onEvent: EventHandler,
	signal?: AbortSignal
) {
	const response = await fetch(`/api/method/${methodPath}`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"X-Frappe-CSRF-Token": frappe.csrf_token,
		},
		credentials: "same-origin",
		body: JSON.stringify(body),
		signal,
	});
	const data = await response.json().catch(() => ({}));
	if (!response.ok || data.exc) {
		throw new Error(serverMessage(data) || __("Request failed ({0})", [response.status]));
	}
	const init = data.message ?? data;

	const streamResponse = await fetch(init.stream_url, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Authorization: `Bearer ${init.token}`,
		},
		body: JSON.stringify(streamBody || {}),
		signal,
	});
	if (!streamResponse.ok) {
		const streamError = await streamResponse.json().catch(() => ({}));
		throw new Error(
			streamError.detail ||
				serverMessage(streamError) ||
				__("Request failed ({0})", [streamResponse.status])
		);
	}
	if (!streamResponse.body) {
		throw new Error(__("Request failed ({0})", [streamResponse.status]));
	}

	await consumeStream(streamResponse.body, onEvent);
}

async function consumeStream(body: ReadableStream<Uint8Array>, onEvent: EventHandler) {
	const reader = body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";

	for (;;) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const blocks = buffer.split("\n\n");
		buffer = blocks.pop() || "";
		for (const block of blocks) {
			const line = block.split("\n").find((item) => item.startsWith("data: "));
			if (!line) continue;
			onEvent(normalizeEvent(JSON.parse(line.slice(6))));
		}
	}
}

function normalizeEvent(event: any) {
	switch (event.type) {
		case "run_started":
			return { ...event, name: event.run };
		case "text":
			return { ...event, delta: event.content || "" };
		default:
			return event;
	}
}

export const startRun = (body: Record<string, any>, onEvent: EventHandler, signal?: AbortSignal) =>
	openRun("frappe_ai.api.frontend.start_run", body, {}, onEvent, signal);

export const resumeRun = (body: Record<string, any>, onEvent: EventHandler, signal?: AbortSignal) =>
	openRun(
		"frappe_ai.api.frontend.resume_run",
		{ run: body.run_name, answers: body.answers },
		{ answers: body.answers },
		onEvent,
		signal
	);
