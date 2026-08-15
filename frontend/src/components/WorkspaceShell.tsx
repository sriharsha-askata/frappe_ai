import React, { useEffect, useMemo, useRef, useState } from "react";
import { Ellipsis, Maximize2, Menu, Minimize2, PanelRightClose, PanelsTopLeft, Server, Wrench } from "lucide-react";
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
		transcript,
		openAgentInspector,
		openMcpInspector,
		openToolInspector,
		openActivityInspector,
		closeInspector,
	} = useStore();
	const [sidebarOpen, setSidebarOpen] = useState(variant === "standalone");
	const [actionsOpen, setActionsOpen] = useState(false);
	const actionsRef = useRef<HTMLDivElement | null>(null);

	const latestExecution = useMemo(
		() =>
			[...transcript]
				.reverse()
				.find((item) => item.role === "assistant" && item.executions.length)?.executions[0] || null,
		[transcript]
	);

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

	function handleOpenActivity() {
		setActionsOpen(false);
		openActivityInspector();
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
							className="faip-icon-button faip-workspace-menu-button"
							onClick={() => setSidebarOpen((value) => !value)}
						>
							<Menu size={16} />
						</button>
						<button type="button" className="faip-workspace-agent" onClick={openAgentInspector}>
							<div className="faip-workspace-agent-title" title={agentRecord?.title || __("Agent")}>
								{agentRecord?.title || __("Agent")}
							</div>
							<div className="faip-workspace-agent-meta" title={agentRecord?.model?.title || __("Agent default")}>
								{agentRecord?.readiness.label || __("Unavailable")} · {agentRecord?.model?.title || __("Agent default")}
							</div>
						</button>
					</div>
					<div className="faip-workspace-header-right" ref={actionsRef}>
						<span className="faip-status-pill faip-workspace-status">{runStatusLabel(currentRun, sending)}</span>
						<button
							type="button"
							className="faip-secondary-button faip-header-action is-tools"
							onClick={handleOpenTools}
							disabled={!agentRecord?.tools.summaries.length}
						>
							<Wrench size={14} />
							{__("Tools")}
						</button>
						<button
							type="button"
							className="faip-secondary-button faip-header-action is-mcp"
							onClick={handleOpenMcps}
							disabled={!agentRecord?.mcp_connections.length}
						>
							<Server size={14} />
							{__("MCPs")}
						</button>
						<button
							type="button"
							className="faip-secondary-button faip-header-action is-activity"
							onClick={handleOpenActivity}
						>
							<PanelsTopLeft size={14} />
							{__("Activity")}
						</button>
						<div className={`faip-overflow ${actionsOpen ? "is-open" : ""}`}>
							<button
								type="button"
								className="faip-secondary-button faip-header-action faip-header-overflow-button"
								onClick={() => setActionsOpen((value) => !value)}
								aria-expanded={actionsOpen}
								aria-label={__("More actions")}
							>
								<Ellipsis size={14} />
							</button>
							{actionsOpen ? (
								<div className="faip-overflow-menu">
									<button type="button" className="faip-menu-item" onClick={handleOpenTools} disabled={!agentRecord?.tools.summaries.length}>
										<Wrench size={14} />
										{__("Tools")}
									</button>
									<button type="button" className="faip-menu-item" onClick={handleOpenMcps} disabled={!agentRecord?.mcp_connections.length}>
										<Server size={14} />
										{__("MCPs")}
									</button>
									<button type="button" className="faip-menu-item" onClick={handleOpenActivity}>
										<PanelsTopLeft size={14} />
										{__("Activity")}
									</button>
								</div>
							) : null}
						</div>
						{variant === "panel" && panelControls ? (
							<div className="faip-panel-controls">
								<button type="button" className="faip-icon-button" onClick={panelControls.onToggleFullscreen}>
									{panelControls.fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
								</button>
								<button type="button" className="faip-icon-button" onClick={panelControls.onClose}>
									<PanelRightClose size={16} />
								</button>
							</div>
						) : null}
					</div>
				</header>

				<div className="faip-workspace-context">
					<div>
						<div className="faip-workspace-context-title">{currentSession?.title || __("New chat")}</div>
						<div className="faip-workspace-context-meta">
							{latestExecution
								? latestExecution.kind === "mcp_tool"
									? __("Latest activity: {0} via {1}", [latestExecution.display_title, latestExecution.connection_name || __("MCP")])
									: __("Latest activity: {0}", [latestExecution.display_title])
								: __("Workspace transcript and execution details stay in sync here.")}
						</div>
					</div>
				</div>

				<div className="faip-workspace-content">
					<MessageList />
					<Composer />
				</div>
			</div>
			<InspectorDrawer />
		</div>
	);
}
