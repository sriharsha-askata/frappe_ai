import React, { useEffect, useRef, useState } from "react";
import { History, Search } from "lucide-react";
import { useStore } from "../state/store";

export function SessionsMenu() {
	const { recentSessions, switchSession, searchSessions } = useStore();
	const [open, setOpen] = useState(false);
	const [query, setQuery] = useState("");
	const [items, setItems] = useState(recentSessions);
	const ref = useRef<HTMLDivElement | null>(null);

	useEffect(() => setItems(recentSessions), [recentSessions]);

	useEffect(() => {
		function onClick(event: MouseEvent) {
			if (!ref.current?.contains(event.target as Node)) setOpen(false);
		}
		document.addEventListener("mousedown", onClick);
		return () => document.removeEventListener("mousedown", onClick);
	}, []);

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
		<div className="faip-menu" ref={ref}>
			<button type="button" className="faip-secondary-button" onClick={() => setOpen((value) => !value)}>
				<History size={14} />
				<span>Sessions</span>
			</button>
			{open ? (
				<div className="faip-menu-popover">
					<div className="faip-search">
						<Search size={14} />
						<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search sessions" />
					</div>
					<div className="faip-menu-list">
						{items.map((item) => (
							<button
								type="button"
								key={item.name}
								className="faip-menu-item"
								onClick={() => {
									setOpen(false);
									void switchSession(item.name);
								}}
							>
								<span className="faip-menu-item-title">{item.title || item.name}</span>
								<small>{item.modified || ""}</small>
							</button>
						))}
					</div>
				</div>
			) : null}
		</div>
	);
}
