import { __ } from "../lib/translate";
import {
	executionStatusFromResult,
	parseArgs,
	parseToolIdentity,
	resultSummary,
	summarizeValues,
	toolLabel,
} from "../lib/toolMeta";
import type { RunEvent } from "../api/stream";
import type { ExecutionItem, Question, SessionRecord, StoreState, TranscriptMessage } from "./types";

function prepareQuestions(questions: Question[]) {
	return (questions || []).map((question) => ({ ...question, _answer: undefined }));
}

function cloneExecution(item: ExecutionItem): ExecutionItem {
	return { ...item, input_summary: item.input_summary.map((row) => ({ ...row })) };
}

export function cloneTranscript(messages: TranscriptMessage[]): TranscriptMessage[] {
	return messages.map((message) =>
		message.role === "user"
			? { ...message, attachments: [...message.attachments] }
			: {
					...message,
					executions: message.executions.map(cloneExecution),
					questions: message.questions.map((question) => ({ ...question })),
					feedback: message.feedback ? { ...message.feedback } : null,
			  }
	);
}

export function findAssistant(messageId: string, messages: TranscriptMessage[]) {
	return messages.find((item) => item.role === "assistant" && item.id === messageId) as
		| Extract<TranscriptMessage, { role: "assistant" }>
		| undefined;
}

function buildExecution(
	id: string,
	rawName: string,
	args: unknown,
	existing?: Partial<ExecutionItem>
): ExecutionItem {
	const identity = parseToolIdentity(rawName);
	return {
		id,
		kind: identity.kind,
		tool_name: identity.toolName,
		display_title: toolLabel(identity.toolName),
		connection_name: identity.connectionName,
		status: existing?.status || "running",
		duration_ms: existing?.duration_ms ?? null,
		input_summary: summarizeValues(parseArgs(args)),
		result_summary: existing?.result_summary || "",
		raw_input: parseArgs(args),
		raw_output: existing?.raw_output ?? null,
		error: existing?.error ?? null,
		approval_status: existing?.approval_status ?? null,
	};
}

export function applyRunEvent(state: StoreState, event: RunEvent, messageId: string): StoreState {
	const transcript = cloneTranscript(state.transcript);
	const message = findAssistant(messageId, transcript);
	if (!message) return state;
	switch (event.type) {
		case "run_started":
			message.run = event.name;
			return {
				...state,
				currentRun: { run: event.name, status: "Running" },
				currentSession:
					state.currentSession ||
					({
						id: event.session,
						name: event.session,
						title: __("New chat"),
						preview: __("New chat"),
					} as SessionRecord),
				transcript,
			};
		case "text":
			message.content += event.delta || "";
			return { ...state, transcript };
		case "tool_started": {
			const existing = message.executions.find((item) => item.id === event.id);
			if (existing) {
				Object.assign(
					existing,
					buildExecution(event.id, event.name, event.arguments, { ...existing, status: "running" })
				);
			} else {
				message.executions.push(buildExecution(event.id, event.name, event.arguments));
			}
			return { ...state, transcript };
		}
		case "tool_ended": {
			const execution = message.executions.find((item) => item.id === event.id);
			if (!execution) return state;
			execution.raw_output = event.result;
			execution.result_summary = resultSummary(event.result);
			const parsed = executionStatusFromResult(event.result);
			execution.status = parsed.status;
			execution.error = parsed.error;
			execution.approval_status = parsed.approvalStatus;
			return { ...state, transcript };
		}
		case "done":
			message.pending = false;
			message.run = message.run || state.currentRun?.run || null;
			if (event.status === "Paused") {
				message.questions = prepareQuestions(event.questions || []);
				for (const execution of message.executions) {
					if (message.questions.some((question) => question.key === execution.id)) {
						execution.status = "awaiting_confirmation";
					}
				}
			}
			return {
				...state,
				currentRun: state.currentRun
					? { ...state.currentRun, status: event.status || "Completed" }
					: state.currentRun,
				transcript,
			};
		case "error":
			message.error = event.message;
			message.pending = false;
			return {
				...state,
				currentRun: state.currentRun
					? { ...state.currentRun, status: "Failed", error: event.message }
					: state.currentRun,
				transcript,
			};
		default:
			return state;
	}
}
