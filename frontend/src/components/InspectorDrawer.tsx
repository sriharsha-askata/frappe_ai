import React from "react";
import { ExternalLink, PanelRightClose, PlayCircle } from "lucide-react";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

function JsonBlock({ value }: { value: any }) {
	if (value === undefined || value === null || value === "") return null;
	return <pre className="faip-code-block">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

export function InspectorDrawer() {
	const {
		inspector,
		closeInspector,
		openToolInspector,
		openMcpInspector,
		openExecutionInspector,
		testMcpConnection,
	} = useStore();
	if (!inspector.open) return null;

	const payload = inspector.payload;

	return (
		<aside className="faip-inspector">
			<div className="faip-inspector-header">
				<div>
					<div className="faip-inspector-eyebrow">{inspector.mode}</div>
					<div className="faip-inspector-title">{inspector.title}</div>
				</div>
				<button type="button" className="faip-icon-button" onClick={closeInspector}>
					<PanelRightClose size={16} />
				</button>
			</div>

			<div className="faip-inspector-body">
				{inspector.mode === "agent" ? (
					<>
						<div className="faip-inspector-stat-grid">
							<div className="faip-inspector-stat">
								<span>{__("Status")}</span>
								<strong>{payload.readiness.label}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Model")}</span>
								<strong>{payload.model.title || payload.model.name}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Tools")}</span>
								<strong>{payload.tools.count}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("MCPs")}</span>
								<strong>{payload.mcp_connections.length}</strong>
							</div>
						</div>
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("Prompt summary")}</div>
							<p>{payload.prompt_summary}</p>
						</div>
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("Tools")}</div>
							<div className="faip-inspector-list">
								{payload.tools.summaries.map((tool: any) => (
									<button key={tool.name} type="button" className="faip-inspector-row" onClick={() => openToolInspector(tool)}>
										<div>
											<div className="faip-inspector-row-title">{tool.display_name}</div>
											<div className="faip-inspector-row-meta">{tool.summary}</div>
										</div>
									</button>
								))}
							</div>
						</div>
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("MCP connections")}</div>
							<div className="faip-inspector-list">
								{payload.mcp_connections.map((connection: any) => (
									<button
										key={connection.name}
										type="button"
										className="faip-inspector-row"
										onClick={() => openMcpInspector(connection)}
									>
										<div>
											<div className="faip-inspector-row-title">{connection.display_name}</div>
											<div className="faip-inspector-row-meta">{connection.status_message || connection.status}</div>
										</div>
									</button>
								))}
							</div>
						</div>
						{payload.configure_action ? (
							<a className="faip-primary-button" href={payload.configure_action.target}>
								<ExternalLink size={14} />
								{payload.configure_action.label}
							</a>
						) : null}
					</>
				) : null}

				{inspector.mode === "model" ? (
					<>
						<div className="faip-inspector-stat-grid">
							<div className="faip-inspector-stat">
								<span>{__("Model")}</span>
								<strong>{payload.model.title || payload.model.name}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Agent")}</span>
								<strong>{payload.agent.title}</strong>
							</div>
						</div>
						<p>{__("Model selection is metadata for this workspace. Switch agents from navigation to change the default.")}</p>
					</>
				) : null}

				{inspector.mode === "mcp" ? (
					<>
						<div className="faip-inspector-stat-grid">
							<div className="faip-inspector-stat">
								<span>{__("Status")}</span>
								<strong>{payload.status}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Transport")}</span>
								<strong>{payload.transport}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Tools")}</span>
								<strong>{payload.tool_count ?? "—"}</strong>
							</div>
						</div>
						<p>{payload.status_message || __("No status details reported.")}</p>
						<button
							type="button"
							className="faip-secondary-button"
							disabled={!payload.test_connection_supported}
							onClick={() => void testMcpConnection(payload.name)}
						>
							<PlayCircle size={14} />
							{__("Test connection")}
						</button>
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("Tool list")}</div>
							{payload.tool_summaries_available && payload.tool_summaries.length ? (
								<div className="faip-inspector-list">
									{payload.tool_summaries.map((tool: any) => (
										<button key={tool.name} type="button" className="faip-inspector-row" onClick={() => openToolInspector(tool)}>
											<div className="faip-inspector-row-title">{tool.display_name || tool.name}</div>
										</button>
									))}
								</div>
							) : (
								<p>{__("Tool metadata is not available for this connection yet.")}</p>
							)}
						</div>
					</>
				) : null}

				{inspector.mode === "tool" ? (
					<>
						<div className="faip-inspector-stat-grid">
							<div className="faip-inspector-stat">
								<span>{__("Confirmation")}</span>
								<strong>{payload.requires_confirmation ? __("Required") : __("Not required")}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Fields")}</span>
								<strong>{payload.schema_summary?.length || 0}</strong>
							</div>
						</div>
						<p>{payload.description || payload.summary}</p>
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("Input schema")}</div>
							<div className="faip-inspector-list">
								{payload.schema_summary?.map((field: any) => (
									<div key={field.name} className="faip-inspector-row is-static">
										<div className="faip-inspector-row-title">
											{field.name} <small>{field.type}</small>
										</div>
										<div className="faip-inspector-row-meta">{field.description || __("No description")}</div>
									</div>
								))}
							</div>
							<JsonBlock value={payload.input_schema} />
						</div>
					</>
				) : null}

				{inspector.mode === "execution" ? (
					<>
						<div className="faip-inspector-stat-grid">
							<div className="faip-inspector-stat">
								<span>{__("Status")}</span>
								<strong>{payload.execution.status}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Type")}</span>
								<strong>{payload.execution.kind}</strong>
							</div>
							<div className="faip-inspector-stat">
								<span>{__("Tool")}</span>
								<strong>{payload.execution.tool_name}</strong>
							</div>
						</div>
						{payload.tool ? (
							<button type="button" className="faip-secondary-button" onClick={() => openToolInspector(payload.tool)}>
								{__("Open tool")}
							</button>
						) : null}
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("Inputs")}</div>
							<JsonBlock value={payload.execution.raw_input} />
						</div>
						<div className="faip-inspector-section">
							<div className="faip-inspector-section-title">{__("Output")}</div>
							<JsonBlock value={payload.execution.raw_output} />
						</div>
					</>
				) : null}

				{inspector.mode === "activity" ? (
					<div className="faip-inspector-list">
						{payload.map((item: any) => (
							<button
								key={item.execution.id}
								type="button"
								className="faip-inspector-row"
								onClick={() => openExecutionInspector(item.execution, item.message)}
							>
								<div>
									<div className="faip-inspector-row-title">{item.execution.display_title}</div>
									<div className="faip-inspector-row-meta">{item.execution.result_summary || item.execution.status}</div>
								</div>
							</button>
						))}
					</div>
				) : null}
			</div>
		</aside>
	);
}
