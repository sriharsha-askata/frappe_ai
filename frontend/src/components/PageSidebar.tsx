import React, { useEffect, useMemo, useRef, useState } from "react";
import { Bot, Check, ChevronDown, MessageSquarePlus, Pin, PinOff, Search, X } from "lucide-react";
import { useStore } from "../state/store";
import type { SessionRecord } from "../state/types";
import { groupSessions } from "../lib/sessionGroups";
import { __ } from "../lib/translate";

function AgentSwitcher({ onNavigate }: { onNavigate: () => void }) {
	const { agents, selectedAgent, setAgent } = useStore();
	const [open, setOpen] = useState(false);
	const ref = useRef<HTMLDivElement | null>(null);
	const current = agents.find((agent) => agent.name === selectedAgent);

	useEffect(() => {
		if (!open) return;
		const onPointerDown = (event: MouseEvent) => {
			if (!ref.current?.contains(event.target as Node)) setOpen(false);
		};
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === "Escape") setOpen(false);
		};
		document.addEventListener("mousedown", onPointerDown);
		document.addEventListener("keydown", onKeyDown);
		return () => {
			document.removeEventListener("mousedown", onPointerDown);
			document.removeEventListener("keydown", onKeyDown);
		};
	}, [open]);

	return (
		<div className="faip-rail-agent" ref={ref}>
			<button
				type="button"
				className="faip-rail-agent-button"
				aria-haspopup="listbox"
				aria-expanded={open}
				onClick={() => setOpen((value) => !value)}
			>
				<span className="faip-rail-agent-mark">
					<Bot size={14} />
				</span>
				<span className="faip-rail-agent-copy">
					<span className="faip-rail-agent-name">{current?.title || __("Choose an agent")}</span>
					<span className="faip-rail-agent-meta">{current?.readiness.label || __("Unavailable")}</span>
				</span>
				<ChevronDown size={14} />
			</button>
			{open ? (
				<div className="faip-rail-menu" role="listbox">
					{agents.map((agent) => (
						<button
							key={agent.name}
							type="button"
							role="option"
							aria-selected={agent.name === selectedAgent}
							className="faip-rail-menu-item"
							onClick={() => {
								setAgent(agent.name);
								setOpen(false);
								onNavigate();
							}}
						>
							<span className="faip-rail-agent-copy">
								<span className="faip-rail-agent-name">{agent.title}</span>
								<span className="faip-rail-agent-meta">{agent.readiness.label}</span>
							</span>
							{agent.name === selectedAgent ? <Check size={14} /> : null}
						</button>
					))}
				</div>
			) : null}
		</div>
	);
}

function SessionRow({
	item,
	active,
	onOpen,
}: {
	item: SessionRecord;
	active: boolean;
	onOpen: () => void;
}) {
	const { togglePinned } = useStore();
	const label = item.pinned ? __("Unpin chat") : __("Pin chat");
	return (
		<div className={`faip-rail-row ${active ? "is-active" : ""} ${item.pinned ? "is-pinned" : ""}`}>
			<button type="button" className="faip-rail-row-title" onClick={onOpen} title={item.title}>
				{item.title}
			</button>
			<button
				type="button"
				className="faip-rail-row-pin"
				onClick={() => void togglePinned(item.name)}
				aria-label={label}
				title={label}
			>
				{item.pinned ? <PinOff size={13} /> : <Pin size={13} />}
			</button>
		</div>
	);
}

export function PageSidebar({ onClose, onNavigate }: { onClose: () => void; onNavigate: () => void }) {
	const { history, currentSession, switchSession, newChat, searchSessions } = useStore();
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

	const groups = useMemo(() => groupSessions(items), [items]);

	return (
		<aside className="faip-nav">
			<div className="faip-rail-header">
				<div className="faip-header-mark">
					<Bot size={16} />
				</div>
				<div className="faip-rail-brand">Frappe AI</div>
				<button
					type="button"
					className="faip-icon-button faip-nav-close"
					onClick={onClose}
					aria-label={__("Close sidebar")}
				>
					<X size={16} />
				</button>
			</div>

			<button
				type="button"
				className="faip-rail-new"
				onClick={() => {
					newChat();
					onNavigate();
				}}
			>
				<MessageSquarePlus size={15} />
				{__("New chat")}
			</button>

			<AgentSwitcher onNavigate={onNavigate} />

			<label className="faip-page-search">
				<Search size={14} />
				<input
					value={query}
					onChange={(event) => setQuery(event.target.value)}
					placeholder={__("Search chats")}
					aria-label={__("Search chats")}
				/>
			</label>

			<nav className="faip-rail-sessions" aria-label={__("Chats")}>
				{groups.length === 0 ? <div className="faip-rail-empty">{__("No chats yet")}</div> : null}
				{groups.map(([label, sessions]) => (
					<section key={label} className="faip-rail-group">
						<div className="faip-rail-group-label">{label}</div>
						{sessions.map((item) => (
							<SessionRow
								key={item.name}
								item={item}
								active={item.name === currentSession?.name}
								onOpen={() => {
									void switchSession(item.name);
									onNavigate();
								}}
							/>
						))}
					</section>
				))}
			</nav>
		</aside>
	);
}
