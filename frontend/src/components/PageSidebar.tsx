import React, { useEffect, useState } from "react";
import { Bot, Plus, Search, X } from "lucide-react";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

export function PageSidebar({ onClose }: { onClose: () => void }) {
	const { recentSessions, sessionName, switchSession, newChat, searchSessions } = useStore();
	const [query, setQuery] = useState("");
	const [items, setItems] = useState(recentSessions);

	useEffect(() => setItems(recentSessions), [recentSessions]);

	useEffect(() => {
		let active = true;
		void searchSessions(query).then((rows) => {
			if (active) setItems(rows);
		});
		return () => {
			active = false;
		};
	}, [query, searchSessions]);

	return (
		<aside className="faip-page-sidebar">
			<div className="faip-page-sidebar-header">
				<div className="faip-header-mark">
					<Bot size={18} />
				</div>
				<div className="faip-page-sidebar-title">Frappe AI</div>
				<button type="button" className="faip-icon-button" onClick={onClose} aria-label="Close sidebar">
					<X size={16} />
				</button>
			</div>

			<button type="button" className="faip-page-new-chat" onClick={newChat}>
				<Plus size={15} />
				{__("New chat")}
			</button>

			<div className="faip-page-search">
				<Search size={14} />
				<input
					value={query}
					onChange={(event) => setQuery(event.target.value)}
					placeholder={__("Search sessions")}
				/>
			</div>

			<div className="faip-page-session-list">
				{items.map((item) => (
					<button
						type="button"
						key={item.name}
						className={`faip-page-session-item ${item.name === sessionName ? "is-active" : ""}`}
						onClick={() => void switchSession(item.name)}
					>
						<span className="faip-page-session-item-title">{item.title || item.name}</span>
						{item.modified ? <small>{item.modified}</small> : null}
					</button>
				))}
			</div>
		</aside>
	);
}
