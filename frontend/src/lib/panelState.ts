const KEY = "frappe-ai-panel-state";

export type PersistedPanelState = {
	open?: boolean;
	fullscreen?: boolean;
	width?: number;
	session?: string | null;
};

export function readPanelState(): PersistedPanelState {
	try {
		return JSON.parse(localStorage.getItem(KEY) || "{}");
	} catch {
		return {};
	}
}

export function writePanelState(state: PersistedPanelState) {
	try {
		localStorage.setItem(KEY, JSON.stringify(state));
	} catch {
		// best effort only
	}
}
