import React from "react";
import { Bot, Maximize2, Minimize2, PanelRightClose, Plus, Sparkles } from "lucide-react";
import { SessionsMenu } from "./SessionsMenu";
import { __ } from "../lib/translate";

export function PanelHeader({
	fullscreen,
	onClose,
	onToggleFullscreen,
	onNewChat,
	mode = "panel",
}: {
	fullscreen: boolean;
	onClose: () => void;
	onToggleFullscreen: () => void;
	onNewChat: () => void;
	mode?: "panel" | "page";
}) {
	return (
		<div className="faip-header">
			<div className="faip-header-brand">
				<div className="faip-header-mark">
					<Bot size={18} />
				</div>
				<div className="faip-header-copy">
					<div className="faip-header-title">Frappe AI</div>
					<div className="faip-header-subtitle">
						{mode === "page" ? __("Site workspace for agents, runs, and approvals") : __("AI workbench")}
					</div>
				</div>
			</div>
			<div className="faip-header-actions">
				<div className="faip-header-status">
					<span className="faip-status-pill is-primary">
						<Sparkles size={12} />
						{__("Live")}
					</span>
					<span className="faip-status-pill">{mode === "page" ? __("Full page") : __("Slide-in panel")}</span>
				</div>
				<SessionsMenu />
				<button type="button" className="faip-secondary-button" onClick={onNewChat}>
					<Plus size={14} />
					{__("New chat")}
				</button>
				{mode === "panel" ? (
					<>
						<button type="button" className="faip-icon-button" onClick={onToggleFullscreen}>
							{fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
						</button>
						<button type="button" className="faip-icon-button" onClick={onClose}>
							<PanelRightClose size={16} />
						</button>
					</>
				) : null}
			</div>
		</div>
	);
}
