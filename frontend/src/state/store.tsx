import React, {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useReducer,
	useRef,
} from "react";
import * as api from "../api/client";
import { resumeRun, startRun } from "../api/stream";
import { readPanelState } from "../lib/panelState";
import { normalizeToolName } from "../lib/toolMeta";
import { __ } from "../lib/translate";

type AgentModel = { name: string; title?: string };
type SessionRow = { name: string; title?: string; modified?: string };
type AttachmentItem = {
	uid: string;
	file: string | null;
	file_name: string;
	file_size?: number;
	status: "uploading" | "ready" | "error";
	error: string;
};
type Feedback = { rating: string; comment: string } | null;
type Question = {
	key: string;
	name: string;
	arguments?: Record<string, any>;
	prompt: string;
	_answer?: string;
	_showOther?: boolean;
	_otherText?: string;
};
type Part =
	| { id: string; type: "text"; text: string }
	| {
			id: string;
			type: "tool";
			name: string;
			arguments?: any;
			result: string | null;
			approval: "approved" | "denied" | "redirected" | null;
	  };
type UserMessage = {
	id: string;
	role: "user";
	content: string;
	attachments: Array<{ file_name: string; file_size?: number }>;
	interrupted?: boolean;
};
type AssistantMessage = {
	id: string;
	role: "assistant";
	parts: Part[];
	pending: boolean;
	questions: Question[];
	runName: string | null;
	feedback: Feedback;
};
type Message = UserMessage | AssistantMessage;

type StoreState = {
	agents: AgentModel[];
	models: AgentModel[];
	recentSessions: SessionRow[];
	supportedFileTypes: string[];
	toolApproval: Record<string, boolean>;
	selectedAgent: string | null;
	selectedModel: string | null;
	sessionName: string | null;
	runName: string | null;
	messages: Message[];
	attachments: AttachmentItem[];
	sending: boolean;
	loaded: boolean;
	scrollTick: number;
	forceScroll: boolean;
	focusTick: number;
};

type StoreContextValue = StoreState & {
	locked: boolean;
	needsSetup: boolean;
	paused: boolean;
	uploading: boolean;
	agentLabel: (name: string | null) => string;
	modelLabel: (name: string | null) => string | null;
	loadInitial: () => Promise<void>;
	restoreSession: () => Promise<void>;
	refreshHistory: () => Promise<void>;
	newChat: () => void;
	switchSession: (name: string) => Promise<void>;
	setAgent: (name: string) => void;
	setModel: (name: string | null) => void;
	send: (text: string) => Promise<void>;
	stopRun: () => void;
	answerQuestion: (messageId: string, questionKey: string, answer: string) => Promise<void>;
	submitFeedback: (runName: string, rating: string, comment?: string) => Promise<void>;
	attachFiles: (files: FileList | null) => void;
	removeAttachment: (uid: string) => void;
	searchSessions: (query: string) => Promise<SessionRow[]>;
	clearForceScroll: () => void;
};

const initialState: StoreState = {
	agents: [],
	models: [],
	recentSessions: [],
	supportedFileTypes: [],
	toolApproval: {},
	selectedAgent: null,
	selectedModel: null,
	sessionName: null,
	runName: null,
	messages: [],
	attachments: [],
	sending: false,
	loaded: false,
	scrollTick: 0,
	forceScroll: false,
	focusTick: 0,
};

const StoreContext = createContext<StoreContextValue | null>(null);

function reducer(_state: StoreState, nextState: StoreState) {
	return nextState;
}

let uid = 0;
let attachmentUid = 0;
const nextId = () => `n${++uid}`;
const nextAttachmentId = () => `a${++attachmentUid}`;

function makeTextPart(text: string): Part {
	return { id: nextId(), type: "text", text };
}

function makeToolPart(id: string, name: string, args: any): Part {
	return {
		id,
		type: "tool",
		name: normalizeToolName(name),
		arguments: args,
		result: null,
		approval: null,
	};
}

function prepareQuestions(questions: Question[]) {
	return (questions || []).map((question) => ({
		...question,
		_showOther: false,
		_otherText: "",
		_answer: undefined,
	}));
}

function parseToolCalls(raw: string | null) {
	if (!raw) return [];
	try {
		return JSON.parse(raw);
	} catch {
		return [];
	}
}

function approvalFromResult(result: string | null) {
	if (typeof result !== "string") return null;
	try {
		const status = JSON.parse(result)?.status;
		if (status === "denied") return "denied";
		if (status === "redirect") return "redirected";
	} catch {
		return null;
	}
	return null;
}

function cloneMessages(messages: Message[]) {
	return messages.map((message) =>
		message.role === "user"
			? { ...message, attachments: [...message.attachments] }
			: {
					...message,
					parts: message.parts.map((part) => ({ ...part })),
					questions: message.questions.map((question) => ({ ...question })),
					feedback: message.feedback ? { ...message.feedback } : null,
			  }
	);
}

export function StoreProvider({
	children,
	onSessionChange,
}: {
	children: React.ReactNode;
	onSessionChange?: (sessionName: string | null) => void;
}) {
	const [state, dispatch] = useReducer(reducer, initialState);
	const stateRef = useRef(state);
	const toolApprovalCache = useRef<Record<string, Record<string, boolean>>>({});
	const sessionRestored = useRef(false);
	const switchSeq = useRef(0);
	const abortController = useRef<AbortController | null>(null);

	const commit = useCallback((next: StoreState) => {
		stateRef.current = next;
		dispatch(next);
	}, []);

	const update = useCallback(
		(fn: (current: StoreState) => StoreState) => {
			commit(fn(stateRef.current));
		},
		[commit]
	);

	const requestScroll = useCallback(
		(force = false) => {
			update((current) => ({
				...current,
				forceScroll: force ? true : current.forceScroll,
				scrollTick: current.scrollTick + 1,
			}));
		},
		[update]
	);

	const focusComposer = useCallback(() => {
		update((current) => ({ ...current, focusTick: current.focusTick + 1 }));
	}, [update]);

	const loadToolApproval = useCallback(
		async (agent: string | null) => {
			if (!agent) {
				update((current) => ({ ...current, toolApproval: {} }));
				return;
			}
			const cached = toolApprovalCache.current[agent];
			if (cached) update((current) => ({ ...current, toolApproval: cached }));
			try {
				const map = await api.getAgentTools(agent);
				toolApprovalCache.current[agent] = map;
				if (stateRef.current.selectedAgent === agent) {
					update((current) => ({ ...current, toolApproval: map }));
				}
			} catch {
				if (!cached && stateRef.current.selectedAgent === agent) {
					update((current) => ({ ...current, toolApproval: {} }));
				}
			}
		},
		[update]
	);

	const pushAssistant = useCallback(
		(pending = true) => {
			let created: AssistantMessage = {
				id: nextId(),
				role: "assistant",
				parts: [],
				pending,
				questions: [],
				runName: null,
				feedback: null,
			};
			update((current) => {
				const nextMessages = cloneMessages(current.messages);
				created = {
					id: nextId(),
					role: "assistant",
					parts: [],
					pending,
					questions: [],
					runName: null,
					feedback: null,
				};
				nextMessages.push(created);
				return { ...current, messages: nextMessages };
			});
			return created.id;
		},
		[update]
	);

	const appendText = useCallback((message: AssistantMessage, delta: string) => {
		const last = message.parts[message.parts.length - 1];
		if (last && last.type === "text") {
			last.text += delta;
		} else if (delta.trim()) {
			message.parts.push(makeTextPart(delta));
		}
	}, []);

	const setToolResult = useCallback(
		(message: AssistantMessage, id: string, result: string | null) => {
			const part = message.parts.find((item) => item.type === "tool" && item.id === id) as
				| Extract<Part, { type: "tool" }>
				| undefined;
			if (!part) return;
			part.result = result;
			if (part.approval === null && stateRef.current.toolApproval[part.name] === true) {
				part.approval = approvalFromResult(result) as any;
			}
		},
		[]
	);

	const findAssistantById = useCallback((messageId: string, messages: Message[]) => {
		return messages.find((item) => item.role === "assistant" && item.id === messageId) as
			| AssistantMessage
			| undefined;
	}, []);

	const refreshHistory = useCallback(async () => {
		const recentSessions = await api.loadHistory().catch(() => []);
		update((current) => ({ ...current, recentSessions }));
	}, [update]);

	const restoreFeedback = useCallback(
		async (feedbackRows: any[] | undefined, seq: number, messages: Message[]) => {
			if (seq !== switchSeq.current || !feedbackRows?.length) return messages;
			const byRun = new Map(feedbackRows.map((row: any) => [row.run, row]));
			return messages.map((message) => {
				if (message.role !== "assistant" || !message.runName) return message;
				const feedback = byRun.get(message.runName);
				return feedback
					? {
						...message,
							feedback: {
								rating: feedback.rating,
								comment: feedback.comment || "",
							},
					  }
					: message;
			});
		},
		[]
	);

	const restorePausedRun = useCallback(
		async (session: string, pausedRun: any) => {
			if (stateRef.current.sessionName !== session || !pausedRun?.questions?.length) return;
			const questions = pausedRun.questions;
			update((current) => {
				const nextMessages = cloneMessages(current.messages);
				const last = [...nextMessages].reverse().find((item) => item.role === "assistant") as
					| AssistantMessage
					| undefined;
				if (!last) return current;
				last.questions = prepareQuestions(questions);
				last.runName = pausedRun.run;
				return { ...current, runName: pausedRun.run, messages: nextMessages };
			});
			requestScroll();
		},
		[requestScroll, update]
	);

	const switchSession = useCallback(
		async (name: string) => {
			if (stateRef.current.sending) return;
			const seq = ++switchSeq.current;
			update((current) => ({
				...current,
				sessionName: name,
				runName: null,
				messages: [],
				attachments: [],
			}));
			await api.recoverSession(name).catch(() => {});
			const doc = await api.getSession(name);
			if (seq !== switchSeq.current) return;
			update((current) => ({
				...current,
				selectedAgent: doc.session?.agent || null,
				selectedModel: doc.session?.model || null,
			}));
			await loadToolApproval(doc.session?.agent || null);
			if (seq !== switchSeq.current) return;

			const attachmentsByRun: Record<string, Array<{ file_name: string; file_size?: number }>> = {};
			for (const attachment of doc.attachments || []) {
				(attachmentsByRun[attachment.run] ||= []).push({
					file_name: attachment.file_name,
					file_size: attachment.file_size,
				});
			}

			const built: Message[] = [];
			let currentAssistant: AssistantMessage | null = null;
			for (const message of doc.messages || []) {
				if (message.role === "user") {
					currentAssistant = null;
					built.push({
						id: nextId(),
						role: "user",
						content: message.content,
						attachments: attachmentsByRun[message.run] || [],
					});
				} else if (message.role === "assistant") {
					if (!currentAssistant) {
						currentAssistant = {
							id: nextId(),
							role: "assistant",
							parts: [],
							pending: false,
							questions: [],
							runName: null,
							feedback: null,
						};
						built.push(currentAssistant);
					}
					if (message.run) currentAssistant.runName = message.run;
					if (message.content) currentAssistant.parts.push(makeTextPart(message.content));
					for (const toolCall of message.tool_calls || []) {
						currentAssistant.parts.push(
							makeToolPart(toolCall.id, toolCall.function.name, toolCall.function.arguments)
						);
					}
				} else if (message.role === "tool" && currentAssistant) {
					setToolResult(currentAssistant, message.tool_call_id, message.content);
				}
			}

			for (let index = 0; index < built.length; index++) {
				if (built[index].role === "user" && built[index + 1]?.role !== "assistant") {
					(built[index] as UserMessage).interrupted = true;
				}
			}

			const withFeedback = await restoreFeedback(doc.feedback, seq, built);
			update((current) => ({ ...current, messages: withFeedback }));
			requestScroll();
			await restorePausedRun(name, doc.paused_run);
		},
		[loadToolApproval, requestScroll, restoreFeedback, restorePausedRun, setToolResult, update]
	);

	const loadInitial = useCallback(async () => {
		try {
			const data = await api.bootstrap();
			const agents = data.agents || [];
			const models = data.models || [];
			const recentSessions = data.recent_sessions || [];
			const assistant = agents.find((item: any) => item.name === "Frappe AI");
			const selectedAgent = assistant ? assistant.name : agents[0]?.name ?? null;
			update((current) => ({
				...current,
				agents,
				models,
				recentSessions,
				supportedFileTypes: data.supported_file_types || [],
				selectedAgent,
				loaded: true,
				focusTick: current.focusTick + 1,
			}));
			await loadToolApproval(selectedAgent);
		} catch {
			frappe.show_alert({
				message: __("Frappe AI failed to load. Refresh the page to retry."),
				indicator: "red",
			});
		}
	}, [loadToolApproval, update]);

	const restoreSession = useCallback(async () => {
		if (sessionRestored.current) return;
		sessionRestored.current = true;
		const { session } = readPanelState();
		if (!session) return;
		try {
			await switchSession(session);
		} catch {
			update((current) => ({ ...current, sessionName: null, runName: null, messages: [] }));
		}
	}, [switchSession, update]);

	const setAgent = useCallback(
		(name: string) => {
			if (stateRef.current.messages.length > 0) return;
			update((current) => ({ ...current, selectedAgent: name }));
			void loadToolApproval(name);
		},
		[loadToolApproval, update]
	);

	const setModel = useCallback((name: string | null) => {
		update((current) => ({ ...current, selectedModel: name || null }));
	}, [update]);

	const newChat = useCallback(() => {
		if (stateRef.current.sending) return;
		update((current) => ({
			...current,
			sessionName: null,
			runName: null,
			messages: [],
			attachments: [],
			focusTick: current.focusTick + 1,
		}));
	}, [update]);

	const searchSessions = useCallback(async (query: string) => {
		if (!query.trim()) return stateRef.current.recentSessions;
		return api.searchSessions(query).catch(() => []);
	}, []);

	const clearForceScroll = useCallback(() => {
		update((current) => (current.forceScroll ? { ...current, forceScroll: false } : current));
	}, [update]);

	const handleEvent = useCallback(
		(event: any, messageId: string) => {
			update((current) => {
				const nextMessages = cloneMessages(current.messages);
				const message = findAssistantById(messageId, nextMessages);
				if (!message) return current;
				switch (event.type) {
					case "run_started":
						message.runName = event.name;
						return {
							...current,
							runName: event.name,
							sessionName: event.session,
							messages: nextMessages,
						};
					case "text":
						appendText(message, event.delta);
						return { ...current, messages: nextMessages };
					case "tool_started": {
						const part = message.parts.find(
							(item) => item.type === "tool" && item.id === event.id
						) as Extract<Part, { type: "tool" }> | undefined;
						if (part) {
							part.arguments = event.arguments;
						} else {
							message.parts.push(makeToolPart(event.id, event.name, event.arguments));
						}
						return { ...current, messages: nextMessages };
					}
					case "tool_ended":
						setToolResult(message, event.id, event.result);
						return { ...current, messages: nextMessages };
					case "done":
						message.pending = false;
						if (event.status === "Paused") {
							message.questions = prepareQuestions(event.questions || []);
							message.runName = current.runName;
						}
						return { ...current, messages: nextMessages };
					case "error":
						appendText(message, `\n\n${__("Error")}: ${event.message}`);
						message.pending = false;
						return { ...current, messages: nextMessages };
					default:
						return current;
				}
			});
			if (event.type === "text" || event.type === "tool_started" || event.type === "tool_ended") {
				requestScroll();
			}
			if (event.type === "done" && event.status === "Paused") requestScroll(true);
			if (event.type === "done") void refreshHistory();
		},
		[appendText, findAssistantById, refreshHistory, requestScroll, setToolResult, update]
	);

	const send = useCallback(
		async (rawText: string) => {
			const text = rawText.trim();
			const current = stateRef.current;
			const paused = Boolean(
				current.messages[current.messages.length - 1] &&
					current.messages[current.messages.length - 1].role === "assistant" &&
					(current.messages[current.messages.length - 1] as AssistantMessage).questions.length
			);
			const uploading = current.attachments.some((item) => item.status === "uploading");
			if (!text || current.sending || paused || uploading) return;

			const ready = current.attachments.filter((item) => item.status === "ready");
			const files = ready.map((item) => item.file).filter(Boolean);
			const chips = ready.map((item) => ({
				file_name: item.file_name,
				file_size: item.file_size,
			}));

			const assistantId = nextId();
			update((stateNow) => ({
				...stateNow,
				attachments: [],
				messages: [
					...cloneMessages(stateNow.messages),
					{ id: nextId(), role: "user", content: text, attachments: chips },
					{
						id: assistantId,
						role: "assistant",
						parts: [],
						pending: true,
						questions: [],
						runName: null,
						feedback: null,
					},
				],
				sending: true,
			}));

			abortController.current = new AbortController();
			requestScroll(true);

			try {
				await startRun(
					{
						input: text,
						...(files.length && { attachments: files }),
						...(current.sessionName && { session: current.sessionName }),
						...(current.selectedAgent && !current.sessionName && { agent: current.selectedAgent }),
						...(current.selectedModel && { model: current.selectedModel }),
					},
					(event) => handleEvent(event, assistantId),
					abortController.current.signal
				);
			} catch (error: any) {
				update((stateNow) => {
					const nextMessages = cloneMessages(stateNow.messages);
					const message = findAssistantById(assistantId, nextMessages);
					if (message) {
						if (error?.name !== "AbortError") {
							appendText(message, `\n\n${__("Error")}: ${error.message}`);
						}
						message.pending = false;
					}
					return { ...stateNow, messages: nextMessages };
				});
			} finally {
				abortController.current = null;
				update((stateNow) => ({
					...stateNow,
					sending: false,
					focusTick: stateNow.focusTick + 1,
				}));
				requestScroll();
			}
		},
		[appendText, findAssistantById, handleEvent, requestScroll, update]
	);

	const resume = useCallback(
		async (answers: Record<string, string>, messageId: string) => {
			const current = stateRef.current;
			const message = current.messages.find(
				(item) => item.role === "assistant" && item.id === messageId
			) as AssistantMessage | undefined;
			const runName = message?.runName || current.runName;
			if (!runName) return;

			update((stateNow) => {
				const nextMessages = cloneMessages(stateNow.messages);
				const target = findAssistantById(messageId, nextMessages);
				if (!target) return stateNow;
				target.questions = [];
				target.pending = true;
				return { ...stateNow, messages: nextMessages, sending: true };
			});
			abortController.current = new AbortController();
			requestScroll(true);

			try {
				await resumeRun(
					{ run_name: runName, answers },
					(event) => handleEvent(event, messageId),
					abortController.current.signal
				);
			} catch (error: any) {
				update((stateNow) => {
					const nextMessages = cloneMessages(stateNow.messages);
					const target = findAssistantById(messageId, nextMessages);
					if (target) {
						if (error?.name !== "AbortError") {
							appendText(target, `\n\n${__("Error")}: ${error.message}`);
						}
						target.pending = false;
					}
					return { ...stateNow, messages: nextMessages };
				});
			} finally {
				abortController.current = null;
				update((stateNow) => ({
					...stateNow,
					sending: false,
					focusTick: stateNow.focusTick + 1,
				}));
				requestScroll();
			}
		},
		[appendText, findAssistantById, handleEvent, requestScroll, update]
	);

	const stopRun = useCallback(() => {
		if (!stateRef.current.sending) return;
		abortController.current?.abort();
		const runName = stateRef.current.runName;
		if (runName) {
			void api.stopRun(runName).catch(() => {});
		} else if (stateRef.current.sessionName) {
			void api.recoverSession(stateRef.current.sessionName).catch(() => {});
		}
	}, []);

	const submitFeedback = useCallback(
		async (runName: string, rating: string, comment = "") => {
			await api.submitFeedback({
				run_name: runName,
				rating,
				comment: comment || null,
			});
			update((current) => {
				const nextMessages = cloneMessages(current.messages);
				for (const message of nextMessages) {
					if (message.role === "assistant" && message.runName === runName) {
						message.feedback = rating === "None" ? null : { rating, comment };
					}
				}
				return { ...current, messages: nextMessages };
			});
		},
		[update]
	);

	const answerQuestion = useCallback(
		async (messageId: string, questionKey: string, answer: string) => {
			const trimmed = answer.trim();
			if (!trimmed) return;
			let answersToSubmit: Record<string, string> | null = null;
			update((current) => {
				const nextMessages = cloneMessages(current.messages);
				const message = findAssistantById(messageId, nextMessages);
				if (!message) return current;
				const question = message.questions.find((item) => item.key === questionKey);
				if (!question) return current;
				question._answer = trimmed;
				const tool = message.parts.find(
					(item) => item.type === "tool" && item.id === questionKey
				) as Extract<Part, { type: "tool" }> | undefined;
				if (tool) {
					tool.approval =
						trimmed === "Approve"
							? "approved"
							: trimmed === "Deny"
								? "denied"
								: "redirected";
				}
				if (!message.questions.some((item) => item._answer === undefined)) {
					answersToSubmit = {};
					for (const item of message.questions) answersToSubmit[item.key] = item._answer!;
				}
				return { ...current, messages: nextMessages };
			});
			if (answersToSubmit) await resume(answersToSubmit, messageId);
		},
		[findAssistantById, resume, update]
	);

	const attachFiles = useCallback(
		(files: FileList | null) => {
			for (const file of Array.from(files || [])) {
				const uid = nextAttachmentId();
				update((current) => ({
					...current,
					attachments: [
						...current.attachments,
						{
							uid,
							file: null,
							file_name: file.name,
							file_size: file.size,
							status: "uploading",
							error: "",
						},
					],
				}));
				void (async () => {
					try {
						const attached = await api.uploadAttachment(file);
						update((current) => ({
							...current,
							attachments: current.attachments.map((item) =>
								item.uid === uid
									? {
											...item,
											file: attached.file,
											file_name: attached.file_name,
											file_size: attached.file_size,
											status: "ready",
											error: "",
									  }
									: item
							),
						}));
					} catch (error: any) {
						update((current) => ({
							...current,
							attachments: current.attachments.map((item) =>
								item.uid === uid
									? { ...item, status: "error", error: error?.message || "" }
									: item
							),
						}));
					}
				})();
			}
		},
		[update]
	);

	const removeAttachment = useCallback((uidToRemove: string) => {
		update((current) => ({
			...current,
			attachments: current.attachments.filter((item) => item.uid !== uidToRemove),
		}));
	}, [update]);

	useEffect(() => {
		stateRef.current = state;
	}, [state]);

	useEffect(() => {
		onSessionChange?.(state.sessionName);
	}, [onSessionChange, state.sessionName]);

	const value = useMemo<StoreContextValue>(() => {
		const locked = state.messages.length > 0;
		const needsSetup = state.loaded && (!state.agents.length || !state.models.length);
		const uploading = state.attachments.some((item) => item.status === "uploading");
		const last = state.messages[state.messages.length - 1];
		const paused =
			last?.role === "assistant" ? Boolean((last as AssistantMessage).questions.length) : false;
		return {
			...state,
			locked,
			needsSetup,
			paused,
			uploading,
			agentLabel: (name) => state.agents.find((item) => item.name === name)?.title || name || "",
			modelLabel: (name) =>
				name ? state.models.find((item) => item.name === name)?.title || name : null,
			loadInitial,
			restoreSession,
			refreshHistory,
			newChat,
			switchSession,
			setAgent,
			setModel,
			send,
			stopRun,
			answerQuestion,
			submitFeedback,
			attachFiles,
			removeAttachment,
			searchSessions,
			clearForceScroll,
		};
	}, [
		answerQuestion,
		attachFiles,
		loadInitial,
		newChat,
		refreshHistory,
		removeAttachment,
		restoreSession,
		searchSessions,
		clearForceScroll,
		send,
		setAgent,
		setModel,
		state,
		stopRun,
		submitFeedback,
		switchSession,
	]);

	return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore() {
	const context = useContext(StoreContext);
	if (!context) throw new Error("useStore must be used within StoreProvider");
	return context;
}
