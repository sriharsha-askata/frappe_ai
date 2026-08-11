import React, { useEffect, useState } from "react";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { PanelHeader } from "./PanelHeader";
import { useStore } from "../state/store";

export function PanelRoot({
	fullscreen,
	onClose,
	onToggleFullscreen,
	mode = "panel",
}: {
	fullscreen: boolean;
	onClose: () => void;
	onToggleFullscreen: () => void;
	mode?: "panel" | "page";
}) {
	const { loadInitial, restoreSession, newChat } = useStore();
	const [composerHeight, setComposerHeight] = useState(108);

	useEffect(() => {
		void loadInitial();
	}, [loadInitial]);

	useEffect(() => {
		void restoreSession();
	}, [restoreSession]);

	return (
		<div
			className={`faip-panel ${mode === "page" ? "is-page" : ""}`}
			style={{ ["--faip-composer-h" as any]: `${composerHeight}px` }}
		>
			<div className="faip-panel-glow faip-panel-glow-a" />
			<div className="faip-panel-glow faip-panel-glow-b" />
			<PanelHeader
				fullscreen={fullscreen}
				onClose={onClose}
				onToggleFullscreen={onToggleFullscreen}
				onNewChat={newChat}
				mode={mode}
			/>
			<MessageList />
			<Composer onHeight={setComposerHeight} />
		</div>
	);
}
