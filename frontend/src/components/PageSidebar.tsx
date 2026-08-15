import React, { useEffect, useMemo, useState } from "react";
import { Bot, ChevronRight, MessageSquarePlus, Search, X } from "lucide-react";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

function bucketLabel(value?: string) {
	if (!value) return __("Earlier");
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return __("Earlier");
	const today = new Date();
	const current = new Date(today.getFullYear(), today.getMonth(), today.getDate());
	const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
	const diff = Math.round((current.getTime() - target.getTime()) / 86400000);
	if (diff === 0) return __("Today");
	if (diff === 1) return __("Yesterday");
	return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function timeLabel(value?: string) {
	if (!value) return "";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "";
	return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function PageSidebar({
	onClose,
	onNavigate,
}: {
	onClose: () => void;
	onNavigate: () => void;
}) {
	const { agents, selectedAgent, setAgent, history, currentSession, switchSession, newChat, searchSessions } = useStore();
	const [query, setQuery] = useState("");
	const [items, setItems] = useState(history);

	useEffect(() => setItems(history), [history]);

	useEffect(() => {
		let active = true;
		void searchSessions(query).then((rows) => {
			if (active) setItems(rows);
		});
		return () => {
			active = false;
		};
	}, [query, searchSessions]);

	const groups = useMemo(() => {
		const output = new Map<string, any[]>();
		for (const item of items) {
			const label = bucketLabel(item.modified);
			const list = output.get(label) || [];
			list.push(item);
			output.set(label, list);
		}
		return Array.from(output.entries());
	}, [items]);

	return (
		<aside className="faip-nav">
			<div className="faip-nav-header">
				<div className="faip-header-mark">
					<Bot size={18} />
				</div>
				<div className="faip-nav-brand">
					<div className="faip-nav-title">Frappe AI</div>
					<div className="faip-nav-subtitle">{__("Navigation")}</div>
				</div>
				<button type="button" className="faip-icon-button faip-nav-close" onClick={onClose} aria-label="Close sidebar">
					<X size={16} />
				</button>
			</div>

			<button
				type="button"
				className="faip-page-new-chat"
				onClick={() => {
					newChat();
					onNavigate();
				}}
			>
				<MessageSquarePlus size={15} />
				{__("New chat")}
			</button>

			<div className="faip-page-search">
				<Search size={14} />
				<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={__("Search sessions")} />
			</div>

			<div className="faip-nav-section">
				<div className="faip-nav-section-title">{__("Agents")}</div>
				<div className="faip-nav-list">
					{agents.map((agent) => (
						<button
							key={agent.name}
							type="button"
							className={`faip-nav-item ${selectedAgent === agent.name ? "is-active" : ""}`}
							onClick={() => {
								setAgent(agent.name);
								onNavigate();
							}}
						>
							<div className="faip-nav-item-copy">
								<div className="faip-nav-item-title">{agent.title}</div>
								<div className="faip-nav-item-meta">{agent.readiness.label}</div>
							</div>
							<ChevronRight size={14} />
						</button>
					))}
				</div>
			</div>

			<div className="faip-nav-section faip-nav-section-sessions">
				<div className="faip-nav-section-title">{__("Sessions")}</div>
				<div className="faip-nav-session-scroll">
					{groups.map(([label, sessions]) => (
						<div key={label} className="faip-nav-group">
							<div className="faip-nav-group-label">{label}</div>
							<div className="faip-nav-list">
								{sessions.map((item: any) => (
									<button
										type="button"
										key={item.name}
										className={`faip-nav-item is-session ${item.name === currentSession?.name ? "is-active" : ""}`}
										onClick={() => {
											void switchSession(item.name);
											onNavigate();
										}}
									>
										<div className="faip-nav-item-copy">
											<div className="faip-nav-item-title">{item.title}</div>
											<div className="faip-nav-item-meta">{timeLabel(item.modified)}</div>
										</div>
										<ChevronRight size={14} />
									</button>
								))}
							</div>
						</div>
					))}
				</div>
			</div>
		</aside>
	);
}
