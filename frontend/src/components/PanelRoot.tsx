import React, { useEffect } from "react";
import { WorkspaceShell } from "./WorkspaceShell";
import { useStore } from "../state/store";

export function PanelRoot({
	fullscreen,
	onClose,
	onToggleFullscreen,
}: {
	fullscreen: boolean;
	onClose: () => void;
	onToggleFullscreen: () => void;
}) {
	const { loadInitial, restoreSession } = useStore();

	useEffect(() => {
		void loadInitial();
	}, [loadInitial]);

	useEffect(() => {
		void restoreSession();
	}, [restoreSession]);

	return (
		<WorkspaceShell
			variant="panel"
			panelControls={{
				fullscreen,
				onClose,
				onToggleFullscreen,
			}}
		/>
	);
}
