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
import { __ } from "../lib/translate";
import {
	executionStatusFromResult,
	parseArgs,
	parseToolIdentity,
	resultSummary,
	summarizeSchema,
	summarizeValues,
	toolLabel,
} from "../lib/toolMeta";

type AgentToolSummary = {
	id: string;
	name: string;
	display_name: string;
	description: string;
	requires_confirmation: boolean;
	input_schema: Record<string, any>;
	summary: string;
};

type MCPAConnection = {
	id: string;
	name: string;
	display_name: string;
	transport: string;
	status: string;
	status_message: string;
	tool_count?: number | null;
	tool_summaries: Array<any>;
	tool_summaries_available?: boolean;
	test_connection_supported?: boolean;
};

type AgentRecord = {
	id: string;
	name: string;
	title: string;
	readiness: { state: string; label: string };
	model: { name: string | null; title: string | null };
	tools: { count: number; summaries: AgentToolSummary[] };
	mcp_connections: MCPAConnection[];
	prompt_summary: string;
	output_summary: string;
	configure_action?: { label: string; target: string };
};

type ModelRecord = { name: string; title: string };
type SessionRecord = {
	id: string;
	name: string;
	title: string;
	preview: string;
	modified?: string;
	agent?: string | null;
	model?: string | null;
	source?: string | null;
};
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
};
type ExecutionItem = {
	id: string;
	kind: "tool" | "mcp_tool";
	tool_name: string;
	display_title: string;
	connection_name?: string | null;
	status: string;
	duration_ms?: number | null;
	input_summary: Array<{ label: string; value: string }>;
	result_summary: string;
	raw_input: any;
	raw_output: any;
	error?: string | null;
	approval_status?: "approved" | "denied" | "redirected" | null;
};
type TranscriptMessage =
	| {
			id: string;
			role: "user";
			content: string;
			run: string | null;
			attachments: Array<{ file_name: string; file_size?: number }>;
			interrupted?: boolean;
	  }
	| {
			id: string;
			role: "assistant";
			run: string | null;
			content: string;
			executions: ExecutionItem[];
			questions: Question[];
			feedback: Feedback;
			pending: boolean;
			error?: string | null;
	  };
type CurrentRun = {
	run: string;
	status: string;
	started_at?: string;
	updated_at?: string;
	error?: string;
} | null;
type InspectorState = {
	open: boolean;
	mode: "agent" | "model" | "mcp" | "tool" | "execution" | "activity";
	title: string;
	payload: any;
};

type StoreState = {
	agents: AgentRecord[];
	models: ModelRecord[];
	history: SessionRecord[];
	selectedAgent: string | null;
	selectedModel: string | null;
	currentSession: SessionRecord | null;
	currentRun: CurrentRun;
	transcript: TranscriptMessage[];
	attachments: AttachmentItem[];
	supportedFileTypes: string[];
	sending: boolean;
	loaded: boolean;
	scrollTick: number;
	forceScroll: boolean;
	focusTick: number;
	inspector: InspectorState;
};

type StoreContextValue = StoreState & {
	locked: boolean;
	needsSetup: boolean;
	paused: boolean;
	uploading: boolean;
	agentRecord: AgentRecord | null;
	loadInitial: () => Promise<void>;
	restoreSession: () => Promise<void>;
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
	searchSessions: (query: string) => Promise<SessionRecord[]>;
	clearForceScroll: () => void;
	openAgentInspector: () => void;
	openModelInspector: () => void;
	openMcpInspector: (connection: MCPAConnection) => void;
	openToolInspector: (tool: AgentToolSummary) => void;
	openExecutionInspector: (execution: ExecutionItem, message?: TranscriptMessage) => void;
	openActivityInspector: () => void;
	closeInspector: () => void;
	testMcpConnection: (name: string) => Promise<void>;
};

export type SessionHostAdapter = {
	getInitialSession?: () => string | null;
	onSessionChange?: (sessionName: string | null) => void;
};

const initialState: StoreState = {
	agents: [],
	models: [],
	history: [],
	selectedAgent: null,
	selectedModel: null,
	currentSession: null,
	currentRun: null,
	transcript: [],
	attachments: [],
	supportedFileTypes: [],
	sending: false,
	loaded: false,
	scrollTick: 0,
	forceScroll: false,
	focusTick: 0,
	inspector: { open: false, mode: "agent", title: "", payload: null },
};

const StoreContext = createContext<StoreContextValue | null>(null);

function reducer(_state: StoreState, nextState: StoreState) {
	return nextState;
}

let uid = 0;
let attachmentUid = 0;
const nextId = () => `n${++uid}`;
const nextAttachmentId = () => `a${++attachmentUid}`;

function prepareQuestions(questions: Question[]) {
	return (questions || []).map((question) => ({ ...question, _answer: undefined }));
}

function cloneExecution(item: ExecutionItem): ExecutionItem {
	return { ...item, input_summary: item.input_summary.map((row) => ({ ...row })) };
}

function cloneTranscript(messages: TranscriptMessage[]) {
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

function requestScrollState(current: StoreState, force = false): StoreState {
	return {
		...current,
		forceScroll: force ? true : current.forceScroll,
		scrollTick: current.scrollTick + 1,
	};
}

function buildExecution(rawName: string, args: any, existing?: Partial<ExecutionItem>): ExecutionItem {
	const identity = parseToolIdentity(rawName);
	return {
		id: existing?.id || nextId(),
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

function setInterrupted(messages: TranscriptMessage[]) {
	for (let index = 0; index < messages.length; index++) {
		const current = messages[index];
		if (current.role !== "user") continue;
		current.interrupted = messages[index + 1]?.role !== "assistant";
	}
	return messages;
}

export function StoreProvider({
	children,
	host,
}: {
	children: React.ReactNode;
	host?: SessionHostAdapter;
}) {
	const [state, dispatch] = useReducer(reducer, initialState);
	const stateRef = useRef(state);
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

	const selectedAgentRecord = useCallback(
		(name: string | null, items = stateRef.current.agents) => items.find((item) => item.name === name) || null,
		[]
	);

	const openInspector = useCallback(
		(mode: InspectorState["mode"], title: string, payload: any) => {
			update((current) => ({ ...current, inspector: { open: true, mode, title, payload } }));
		},
		[update]
	);

	const hydrateFromBootstrap = useCallback(
		(data: any) => {
			const selectedSession = data.session?.current || null;
			update((current) => ({
				...current,
				agents: data.agent?.items || [],
				models: data.agent?.models || [],
				history: data.session?.history || [],
				selectedAgent: data.agent?.selected || null,
				selectedModel: selectedSession?.model || null,
				currentSession: selectedSession,
				currentRun: data.execution?.current_run || null,
				transcript: data.execution?.transcript || [],
				supportedFileTypes: data.composer?.supported_file_types || [],
				loaded: true,
				focusTick: current.focusTick + 1,
			}));
		},
		[update]
	);

	const loadInitial = useCallback(async () => {
		try {
			const data = await api.bootstrap();
			hydrateFromBootstrap(data);
		} catch {
			frappe.show_alert({
				message: __("Frappe AI failed to load. Refresh the page to retry."),
				indicator: "red",
			});
		}
	}, [hydrateFromBootstrap]);

	const refreshHistory = useCallback(async () => {
		const rows = await api.loadHistory().catch(() => []);
		update((current) => ({ ...current, history: rows }));
	}, [update]);

	const switchSession = useCallback(
		async (name: string) => {
			if (stateRef.current.sending) return;
			const seq = ++switchSeq.current;
			update((current) => ({
				...current,
				currentSession: current.history.find((item) => item.name === name) || { id: name, name, title: name, preview: name },
				currentRun: null,
				transcript: [],
				attachments: [],
			}));
			await api.recoverSession(name).catch(() => {});
			const detail = await api.getSession(name);
			if (seq !== switchSeq.current) return;
			update((current) => ({
				...current,
				selectedAgent: detail.agent?.selected || current.selectedAgent,
				selectedModel: detail.session?.current?.model || null,
				currentSession: detail.session?.current || current.currentSession,
				currentRun: detail.execution?.current_run || null,
				transcript: setInterrupted(detail.execution?.transcript || []),
			}));
			update((current) => requestScrollState(current));
		},
		[update]
	);

	const restoreSession = useCallback(async () => {
		if (sessionRestored.current) return;
		sessionRestored.current = true;
		const session = host?.getInitialSession?.() || null;
		if (!session) return;
		try {
			await switchSession(session);
		} catch {
			update((current) => ({ ...current, currentSession: null, currentRun: null, transcript: [] }));
		}
	}, [host, switchSession, update]);

	const setAgent = useCallback(
		(name: string) => {
			if (stateRef.current.transcript.length > 0) return;
			update((current) => ({ ...current, selectedAgent: name }));
		},
		[update]
	);

	const setModel = useCallback((name: string | null) => {
		update((current) => ({ ...current, selectedModel: name || null }));
	}, [update]);

	const newChat = useCallback(() => {
		if (stateRef.current.sending) return;
		update((current) => ({
			...current,
			currentSession: null,
			currentRun: null,
			transcript: [],
			attachments: [],
			selectedModel: null,
			focusTick: current.focusTick + 1,
		}));
	}, [update]);

	const searchSessions = useCallback(async (query: string) => {
		if (!query.trim()) return stateRef.current.history;
		return api.searchSessions(query).catch(() => []);
	}, []);

	const clearForceScroll = useCallback(() => {
		update((current) => (current.forceScroll ? { ...current, forceScroll: false } : current));
	}, [update]);

	const findAssistant = useCallback((messageId: string, messages: TranscriptMessage[]) => {
		return messages.find((item) => item.role === "assistant" && item.id === messageId) as
			| Extract<TranscriptMessage, { role: "assistant" }>
			| undefined;
	}, []);

	const findToolSummary = useCallback((toolName: string) => {
		const agent = selectedAgentRecord(stateRef.current.selectedAgent);
		return agent?.tools.summaries.find((item) => item.name === toolName) || null;
	}, [selectedAgentRecord]);

	const handleEvent = useCallback(
		(event: any, messageId: string) => {
			update((current) => {
				const transcript = cloneTranscript(current.transcript);
				const message = findAssistant(messageId, transcript);
				if (!message) return current;
				switch (event.type) {
					case "run_started":
						message.run = event.name;
						return {
							...current,
							currentRun: { run: event.name, status: "Running" },
							currentSession:
								current.currentSession ||
								({ id: event.session, name: event.session, title: __("New chat"), preview: __("New chat") } as SessionRecord),
							transcript,
						};
					case "text":
						message.content += event.delta || "";
						return { ...current, transcript };
					case "tool_started": {
						const existing = message.executions.find((item) => item.id === event.id);
						if (existing) {
							Object.assign(existing, buildExecution(event.name, event.arguments, { ...existing, id: event.id, status: "running" }));
						} else {
							message.executions.push({ ...buildExecution(event.name, event.arguments), id: event.id });
						}
						return { ...current, transcript };
					}
					case "tool_ended": {
						const execution = message.executions.find((item) => item.id === event.id);
						if (!execution) return current;
						execution.raw_output = event.result;
						execution.result_summary = resultSummary(event.result);
						const parsed = executionStatusFromResult(event.result);
						execution.status = parsed.status;
						execution.error = parsed.error;
						execution.approval_status = parsed.approvalStatus;
						return { ...current, transcript };
					}
					case "done":
						message.pending = false;
						message.run = message.run || current.currentRun?.run || null;
						if (event.status === "Paused") {
							message.questions = prepareQuestions(event.questions || []);
							for (const execution of message.executions) {
								if (message.questions.some((question) => question.key === execution.id)) {
									execution.status = "awaiting_confirmation";
								}
							}
						}
						return {
							...current,
							currentRun: current.currentRun
								? { ...current.currentRun, status: event.status || "Completed" }
								: current.currentRun,
							transcript,
						};
					case "error":
						message.error = event.message;
						message.pending = false;
						return {
							...current,
							currentRun: current.currentRun ? { ...current.currentRun, status: "Failed", error: event.message } : current.currentRun,
							transcript,
						};
					default:
						return current;
				}
			});
			if (["text", "tool_started", "tool_ended"].includes(event.type)) {
				update((current) => requestScrollState(current));
			}
			if (event.type === "done" && event.status === "Paused") {
				update((current) => requestScrollState(current, true));
			}
			if (event.type === "done") void refreshHistory();
		},
		[findAssistant, refreshHistory, update]
	);

	const send = useCallback(
		async (rawText: string) => {
			const text = rawText.trim();
			const current = stateRef.current;
			const paused = current.transcript.some(
				(item) => item.role === "assistant" && item.pending === false && item.questions.length > 0
			);
			const uploading = current.attachments.some((item) => item.status === "uploading");
			if (!text || current.sending || paused || uploading) return;

			const ready = current.attachments.filter((item) => item.status === "ready");
			const files = ready.map((item) => item.file).filter(Boolean);
			const chips = ready.map((item) => ({ file_name: item.file_name, file_size: item.file_size }));
			const assistantId = nextId();
			update((stateNow) => ({
				...stateNow,
				attachments: [],
				transcript: [
					...cloneTranscript(stateNow.transcript),
					{ id: nextId(), role: "user", content: text, run: null, attachments: chips },
					{
						id: assistantId,
						role: "assistant",
						run: null,
						content: "",
						executions: [],
						questions: [],
						feedback: null,
						pending: true,
						error: null,
					},
				],
				sending: true,
			}));

			abortController.current = new AbortController();
			update((currentState) => requestScrollState(currentState, true));

			try {
				await startRun(
					{
						input: text,
						...(files.length && { attachments: files }),
						...(current.currentSession && { session: current.currentSession.name }),
						...(current.selectedAgent && !current.currentSession && { agent: current.selectedAgent }),
						...(current.selectedModel && { model: current.selectedModel }),
					},
					(event) => handleEvent(event, assistantId),
					abortController.current.signal
				);
			} catch (error: any) {
				update((stateNow) => {
					const transcript = cloneTranscript(stateNow.transcript);
					const message = findAssistant(assistantId, transcript);
					if (message) {
						if (error?.name !== "AbortError") {
							message.error = error.message;
						}
						message.pending = false;
					}
					return { ...stateNow, transcript };
				});
			} finally {
				abortController.current = null;
				update((stateNow) => ({
					...requestScrollState(stateNow),
					sending: false,
					focusTick: stateNow.focusTick + 1,
				}));
			}
		},
		[findAssistant, handleEvent, update]
	);

	const resume = useCallback(
		async (answers: Record<string, string>, messageId: string) => {
			const current = stateRef.current;
			const message = current.transcript.find(
				(item) => item.role === "assistant" && item.id === messageId
			) as Extract<TranscriptMessage, { role: "assistant" }> | undefined;
			const runName = message?.run || current.currentRun?.run;
			if (!runName) return;

			update((stateNow) => {
				const transcript = cloneTranscript(stateNow.transcript);
				const target = findAssistant(messageId, transcript);
				if (!target) return stateNow;
				target.questions = [];
				target.pending = true;
				return { ...stateNow, transcript, sending: true };
			});
			abortController.current = new AbortController();
			update((currentState) => requestScrollState(currentState, true));

			try {
				await resumeRun(
					{ run_name: runName, answers },
					(event) => handleEvent(event, messageId),
					abortController.current.signal
				);
			} catch (error: any) {
				update((stateNow) => {
					const transcript = cloneTranscript(stateNow.transcript);
					const target = findAssistant(messageId, transcript);
					if (target) {
						target.pending = false;
						target.error = error?.name !== "AbortError" ? error.message : target.error;
					}
					return { ...stateNow, transcript };
				});
			} finally {
				abortController.current = null;
				update((stateNow) => ({
					...requestScrollState(stateNow),
					sending: false,
					focusTick: stateNow.focusTick + 1,
				}));
			}
		},
		[findAssistant, handleEvent, update]
	);

	const stopRun = useCallback(() => {
		if (!stateRef.current.sending) return;
		abortController.current?.abort();
		const runName = stateRef.current.currentRun?.run;
		if (runName) {
			void api.stopRun(runName).catch(() => {});
		} else if (stateRef.current.currentSession) {
			void api.recoverSession(stateRef.current.currentSession.name).catch(() => {});
		}
	}, []);

	const submitFeedback = useCallback(
		async (runName: string, rating: string, comment = "") => {
			await api.submitFeedback({ run_name: runName, rating, comment: comment || null });
			update((current) => {
				const transcript = cloneTranscript(current.transcript);
				for (const message of transcript) {
					if (message.role === "assistant" && message.run === runName) {
						message.feedback = rating === "None" ? null : { rating, comment };
					}
				}
				return { ...current, transcript };
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
				const transcript = cloneTranscript(current.transcript);
				const message = findAssistant(messageId, transcript);
				if (!message) return current;
				const question = message.questions.find((item) => item.key === questionKey);
				if (!question) return current;
				question._answer = trimmed;
				const execution = message.executions.find((item) => item.id === questionKey);
				if (execution) {
					execution.approval_status =
						trimmed === "Approve" ? "approved" : trimmed === "Deny" ? "denied" : "redirected";
					execution.status = "running";
				}
				if (!message.questions.some((item) => item._answer === undefined)) {
					answersToSubmit = {};
					for (const item of message.questions) answersToSubmit[item.key] = item._answer!;
				}
				return { ...current, transcript };
			});
			if (answersToSubmit) await resume(answersToSubmit, messageId);
		},
		[findAssistant, resume, update]
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
								item.uid === uid ? { ...item, status: "error", error: error?.message || "" } : item
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

	const closeInspector = useCallback(() => {
		update((current) => ({ ...current, inspector: { ...current.inspector, open: false } }));
	}, [update]);

	const openAgentInspector = useCallback(() => {
		const agent = selectedAgentRecord(stateRef.current.selectedAgent);
		if (agent) openInspector("agent", agent.title, agent);
	}, [openInspector, selectedAgentRecord]);

	const openModelInspector = useCallback(() => {
		const agent = selectedAgentRecord(stateRef.current.selectedAgent);
		if (!agent) return;
		openInspector("model", agent.model.title || agent.model.name || __("Model"), {
			agent,
			model: agent.model,
		});
	}, [openInspector, selectedAgentRecord]);

	const openMcpInspector = useCallback((connection: MCPAConnection) => {
		openInspector("mcp", connection.display_name, connection);
	}, [openInspector]);

	const openToolInspector = useCallback(
		(tool: AgentToolSummary) => {
			openInspector("tool", tool.display_name || tool.name, {
				...tool,
				schema_summary: summarizeSchema(tool.input_schema),
			});
		},
		[openInspector]
	);

	const openExecutionInspector = useCallback(
		(execution: ExecutionItem, message?: TranscriptMessage) => {
			openInspector("execution", execution.display_title, {
				execution,
				message,
				tool: findToolSummary(execution.tool_name),
			});
		},
		[findToolSummary, openInspector]
	);

	const openActivityInspector = useCallback(() => {
		const activity = stateRef.current.transcript
			.filter((item) => item.role === "assistant")
			.flatMap((item) => item.executions.map((execution) => ({ execution, message: item })));
		openInspector("activity", __("Activity"), activity);
	}, [openInspector]);

	const testMcpConnection = useCallback(
		async (name: string) => {
			const result = await api.checkMcpConnection(name);
			update((current) => ({
				...current,
				agents: current.agents.map((agent) =>
					agent.name !== current.selectedAgent
						? agent
						: {
								...agent,
								mcp_connections: agent.mcp_connections.map((connection) =>
									connection.name === name
										? {
												...connection,
												status: result.is_connected ? "connected" : "disconnected",
												status_message: result.status_message || "",
										  }
										: connection
								),
						  }
				),
			}));
		},
		[update]
	);

	useEffect(() => {
		stateRef.current = state;
	}, [state]);

	useEffect(() => {
		host?.onSessionChange?.(state.currentSession?.name || null);
	}, [host, state.currentSession?.name]);

	const value = useMemo<StoreContextValue>(() => {
		const locked = state.transcript.length > 0;
		const needsSetup = state.loaded && (!state.agents.length || !state.models.length);
		const uploading = state.attachments.some((item) => item.status === "uploading");
		const paused = state.transcript.some(
			(item) => item.role === "assistant" && item.questions.some((question) => question._answer === undefined)
		);
		return {
			...state,
			locked,
			needsSetup,
			paused,
			uploading,
			agentRecord: selectedAgentRecord(state.selectedAgent, state.agents),
			loadInitial,
			restoreSession,
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
			openAgentInspector,
			openModelInspector,
			openMcpInspector,
			openToolInspector,
			openExecutionInspector,
			openActivityInspector,
			closeInspector,
			testMcpConnection,
		};
	}, [
		answerQuestion,
		attachFiles,
		clearForceScroll,
		closeInspector,
		loadInitial,
		newChat,
		openActivityInspector,
		openAgentInspector,
		openExecutionInspector,
		openMcpInspector,
		openModelInspector,
		openToolInspector,
		removeAttachment,
		restoreSession,
		searchSessions,
		selectedAgentRecord,
		send,
		setAgent,
		setModel,
		state,
		stopRun,
		submitFeedback,
		switchSession,
		testMcpConnection,
	]);

	return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore() {
	const context = useContext(StoreContext);
	if (!context) throw new Error("useStore must be used within StoreProvider");
	return context;
}
