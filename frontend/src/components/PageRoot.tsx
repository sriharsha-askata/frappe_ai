import React, { useEffect, useRef } from "react";
import { WorkspaceShell } from "./WorkspaceShell";
import { useStore } from "../state/store";
import "../styles/page.css";

export function PageRoot() {
	const { loadInitial, restoreSession } = useStore();
	const rootRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		void loadInitial();
	}, [loadInitial]);

	useEffect(() => {
		void restoreSession();
	}, [restoreSession]);

	useEffect(() => {
		const root = rootRef.current;
		if (!root) return;
		const applyTheme = () => {
			const theme = document.documentElement.getAttribute("data-theme") || "light";
			root.setAttribute("data-theme", theme);
		};
		applyTheme();
		const observer = new MutationObserver(applyTheme);
		observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
		return () => observer.disconnect();
	}, []);

	return (
		<div ref={rootRef} className="faip-page">
			<WorkspaceShell variant="standalone" />
		</div>
	);
}
