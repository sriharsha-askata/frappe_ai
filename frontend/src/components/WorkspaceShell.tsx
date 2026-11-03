import React, { useEffect, useRef, useState } from "react";
import { Activity, Ellipsis, Maximize2, Menu, Minimize2, PanelRightClose, Server, Wrench } from "lucide-react";
import { Composer } from "./Composer";
import { InspectorDrawer } from "./InspectorDrawer";
import { MessageList } from "./MessageList";
import { PageSidebar } from "./PageSidebar";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

function runStatusLabel(run: any, sending: boolean) {
	if (sending) return __("Running");
	if (!run) return __("Ready");
	return run.status;
}

/** Colour family for the status pill: running, waiting (needs the user), failed, or neutral. */
function statusTone(run: any, sending: boolean): "running" | "waiting" | "failed" | "idle" {
	if (sending) return "running";
	const status = String(run?.status || "").toLowerCase();
	if (status === "paused") return "waiting";
	if (status === "failed" || status === "error") return "failed";
	return "idle";
}

export function WorkspaceShell({
	variant,
	panelControls,
}: {
	variant: "panel" | "standalone";
	panelControls?: {
		fullscreen: boolean;
		onClose: () => void;
		onToggleFullscreen: () => void;
	};
}) {
	const {
		agentRecord,
		currentSession,
		currentRun,
		inspector,
		sending,
		openAgentInspector,
		openMcpInspector,
		openToolInspector,
		openActivityInspector,
		closeInspector,
	} = useStore();
	const [sidebarOpen, setSidebarOpen] = useState(variant === "standalone");
	const [actionsOpen, setActionsOpen] = useState(false);
	const actionsRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		function onPointerDown(event: MouseEvent) {
			if (!actionsRef.current?.contains(event.target as Node)) setActionsOpen(false);
		}
		document.addEventListener("mousedown", onPointerDown);
		return () => document.removeEventListener("mousedown", onPointerDown);
	}, []);

	function handleOpenTools() {
		setActionsOpen(false);
		if (agentRecord?.tools.summaries.length) openToolInspector(agentRecord.tools.summaries[0]);
	}

	function handleOpenMcps() {
		setActionsOpen(false);
		if (agentRecord?.mcp_connections.length) openMcpInspector(agentRecord.mcp_connections[0]);
	}

	const activityOpen = inspector.open && inspector.mode === "activity";
	const title = currentSession?.title || __("New chat");

	function handleToggleActivity() {
		if (activityOpen) closeInspector();
		else openActivityInspector();
	}

	function dismissSidebarIfMobile() {
		if (typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches) {
			setSidebarOpen(false);
		}
	}

	return (
		<div className={`faip-workspace-shell is-${variant} ${sidebarOpen ? "has-sidebar" : ""}`}>
			<div
				className={`faip-shell-backdrop is-sidebar ${sidebarOpen ? "is-visible" : ""}`}
				onClick={() => setSidebarOpen(false)}
			/>
			<div
				className={`faip-shell-backdrop is-inspector ${inspector.open ? "is-visible" : ""}`}
				onClick={closeInspector}
			/>
			<PageSidebar onClose={() => setSidebarOpen(false)} onNavigate={dismissSidebarIfMobile} />
			<div className="faip-workspace-main">
				<header className="faip-workspace-header">
					<div className="faip-workspace-header-left">
						<button
							type="button"
							className="faip-icon-button"
							onClick={() => setSidebarOpen((value) => !value)}
							aria-label={__("Toggle sidebar")}
							aria-pressed={sidebarOpen}
						>
							<Menu size={16} />
						</button>
						<h1 className="faip-topbar-title" title={title}>
							{title}
						</h1>
						<span className={`faip-status-pill is-${statusTone(currentRun, sending)}`}>
							{runStatusLabel(currentRun, sending)}
						</span>
					</div>
					<div className="faip-workspace-header-right" ref={actionsRef}>
						<button
							type="button"
							className="faip-agent-chip"
							onClick={openAgentInspector}
							title={__("Agent and model settings")}
						>
							<span className="faip-agent-chip-name">{agentRecord?.title || __("Agent")}</span>
							<span className="faip-agent-chip-model">{agentRecord?.model?.title || __("Agent default")}</span>
						</button>
						<button
							type="button"
							className={`faip-icon-button ${activityOpen ? "is-on" : ""}`}
							onClick={handleToggleActivity}
							aria-label={__("Activity")}
							aria-pressed={activityOpen}
							title={__("Activity")}
						>
							<Activity size={16} />
						</button>
						<div className="faip-overflow">
							<button
								type="button"
								className="faip-icon-button"
								onClick={() => setActionsOpen((value) => !value)}
								aria-expanded={actionsOpen}
								aria-label={__("More actions")}
								title={__("More actions")}
							>
								<Ellipsis size={16} />
							</button>
							{actionsOpen ? (
								<div className="faip-overflow-menu">
									<button
										type="button"
										className="faip-menu-item"
										onClick={handleOpenTools}
										disabled={!agentRecord?.tools.summaries.length}
									>
										<Wrench size={14} />
										{__("Tools")}
									</button>
									<button
										type="button"
										className="faip-menu-item"
										onClick={handleOpenMcps}
										disabled={!agentRecord?.mcp_connections.length}
									>
										<Server size={14} />
										{__("MCPs")}
									</button>
								</div>
							) : null}
						</div>
						{variant === "panel" && panelControls ? (
							<div className="faip-panel-controls">
								<button
									type="button"
									className="faip-icon-button"
									onClick={panelControls.onToggleFullscreen}
									aria-label={panelControls.fullscreen ? __("Exit full screen") : __("Full screen")}
								>
									{panelControls.fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
								</button>
								<button
									type="button"
									className="faip-icon-button"
									onClick={panelControls.onClose}
									aria-label={__("Close panel")}
								>
									<PanelRightClose size={16} />
								</button>
							</div>
						) : null}
					</div>
				</header>

				<div className="faip-workspace-content">
					<MessageList />
					<Composer />
				</div>
			</div>
			<InspectorDrawer />
		</div>
	);
}
