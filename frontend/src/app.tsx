import React from "react";
import { PageRoot } from "./components/PageRoot";
import { PanelRoot } from "./components/PanelRoot";
import { SessionHostAdapter, StoreProvider } from "./state/store";

export function App({
	host,
	variant = "panel",
	panel,
}: {
	host?: SessionHostAdapter;
	variant?: "panel" | "standalone";
	panel?: {
		fullscreen: boolean;
		onClose: () => void;
		onToggleFullscreen: () => void;
	};
}) {
	return (
		<StoreProvider host={host}>
			{variant === "standalone" ? (
				<PageRoot />
			) : (
				<PanelRoot
					fullscreen={panel?.fullscreen ?? false}
					onClose={panel?.onClose || (() => {})}
					onToggleFullscreen={panel?.onToggleFullscreen || (() => {})}
				/>
			)}
		</StoreProvider>
	);
}
