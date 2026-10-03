export type AgentToolSummary = {
	id: string;
	name: string;
	display_name: string;
	description: string;
	requires_confirmation: boolean;
	input_schema: Record<string, any>;
	summary: string;
};

export type MCPAConnection = {
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

export type AgentRecord = {
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

export type ModelRecord = { name: string; title: string };
export type SessionRecord = {
	id: string;
	name: string;
	title: string;
	preview: string;
	modified?: string;
	agent?: string | null;
	model?: string | null;
	source?: string | null;
};
export type AttachmentItem = {
	uid: string;
	file: string | null;
	file_name: string;
	file_size?: number;
	status: "uploading" | "ready" | "error";
	error: string;
};
export type Feedback = { rating: string; comment: string } | null;
export type Question = {
	key: string;
	name: string;
	arguments?: Record<string, any>;
	prompt: string;
	_answer?: string;
};
export type ExecutionItem = {
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
export type TranscriptMessage =
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
export type CurrentRun = {
	run: string;
	status: string;
	started_at?: string;
	updated_at?: string;
	error?: string;
} | null;
export type InspectorState = {
	open: boolean;
	mode: "agent" | "model" | "mcp" | "tool" | "execution" | "activity";
	title: string;
	payload: any;
};

export type StoreState = {
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

export type StoreContextValue = StoreState & {
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
