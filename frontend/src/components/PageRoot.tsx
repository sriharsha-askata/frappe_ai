import React, { useEffect, useRef, useState } from "react";
import { Menu, Sparkles } from "lucide-react";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { PageSidebar } from "./PageSidebar";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";
import "../styles/page.css";

// Dedicated full-page shell for /app/frappe-ai (mode="page").
// Owns the outer layout only — the transcript, composer, and session logic are
// shared with the desk panel through the store and the inner components.
export function PageRoot() {
	const { loadInitial, restoreSession, sessionName, recentSessions, runName, sending } = useStore();
	const [sidebarOpen, setSidebarOpen] = useState(true);
	const [composerHeight, setComposerHeight] = useState(120);
	const rootRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		void loadInitial();
	}, [loadInitial]);

	useEffect(() => {
		void restoreSession();
	}, [restoreSession]);

	// Mirror the desk theme onto the page root so the page follows light/dark.
	useEffect(() => {
		const root = rootRef.current;
		if (!root) return;
		const applyTheme = () => {
			const theme = document.documentElement.getAttribute("data-theme") || "light";
			root.setAttribute("data-theme", theme);
		};
		applyTheme();
		const observer = new MutationObserver(applyTheme);
		observer.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["data-theme"],
		});
		return () => observer.disconnect();
	}, []);

	const currentTitle =
		recentSessions.find((item) => item.name === sessionName)?.title || sessionName;

	return (
		<div
			ref={rootRef}
			className={`faip-page ${sidebarOpen ? "has-sidebar" : ""}`}
			style={{ "--faip-composer-h": `${composerHeight}px` } as React.CSSProperties}
		>
			<PageSidebar onClose={() => setSidebarOpen(false)} />
			<div className="faip-page-main">
				<header className="faip-page-topbar">
					<div className="faip-page-topbar-left">
						<button
							type="button"
							className="faip-icon-button"
							onClick={() => setSidebarOpen((value) => !value)}
							aria-label="Toggle sidebar"
						>
							<Menu size={16} />
						</button>
						<span className="faip-page-session-title">{currentTitle || __("New chat")}</span>
					</div>
					<div className="faip-page-topbar-right">
						<span className="faip-status-pill is-primary">
							<Sparkles size={12} />
							{__("Live")}
						</span>
						{sending ? (
							<span className="faip-status-pill">{__("Running")}</span>
						) : runName ? (
							<span className="faip-status-pill">{__("Ready")}</span>
						) : null}
					</div>
				</header>
				<div className="faip-page-content">
					<MessageList />
					<Composer onHeight={setComposerHeight} />
				</div>
			</div>
		</div>
	);
}
