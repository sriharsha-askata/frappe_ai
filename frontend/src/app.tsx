import React from "react";
import { PageRoot } from "./components/PageRoot";
import { PanelRoot } from "./components/PanelRoot";
import { StoreProvider } from "./state/store";

export function App({
	fullscreen,
	onClose,
	onToggleFullscreen,
	onSessionChange,
	mode = "panel",
}: {
	fullscreen: boolean;
	onClose: () => void;
	onToggleFullscreen: () => void;
	onSessionChange: (sessionName: string | null) => void;
	mode?: "panel" | "page";
}) {
	return (
		<StoreProvider onSessionChange={onSessionChange}>
			{mode === "page" ? (
				<PageRoot />
			) : (
				<PanelRoot
					fullscreen={fullscreen}
					onClose={onClose}
					onToggleFullscreen={onToggleFullscreen}
					mode={mode}
				/>
			)}
		</StoreProvider>
	);
}
