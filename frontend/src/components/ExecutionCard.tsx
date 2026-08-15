import React from "react";
import { CheckCircle2, CircleDashed, CircleX, PlayCircle, ShieldAlert, Wrench } from "lucide-react";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

export function ExecutionCard({ execution, message }: { execution: any; message?: any }) {
	const { openExecutionInspector } = useStore();

	const icon =
		execution.status === "awaiting_confirmation" ? (
			<ShieldAlert size={16} />
		) : execution.status === "error" ? (
			<CircleX size={16} />
		) : execution.status === "completed" ? (
			<CheckCircle2 size={16} />
		) : execution.status === "running" ? (
			<CircleDashed size={16} className="faip-spin-slow" />
		) : execution.kind === "mcp_tool" ? (
			<PlayCircle size={16} />
		) : (
			<Wrench size={16} />
		);

	const statusLabel =
		execution.status === "awaiting_confirmation"
			? __("Awaiting confirmation")
			: execution.status === "running"
				? __("Running")
				: execution.status === "error"
					? __("Failed")
					: __("Completed");

	return (
		<button
			type="button"
			className={`faip-execution-card is-${execution.status}`}
			onClick={() => openExecutionInspector(execution, message)}
		>
			<div className="faip-execution-card-header">
				<div className="faip-execution-card-title-wrap">
					<span className="faip-execution-card-icon">{icon}</span>
					<div>
						<div className="faip-execution-card-title">{execution.display_title}</div>
						<div className="faip-execution-card-meta">
							{execution.kind === "mcp_tool"
								? __("MCP: {0}", [execution.connection_name || __("Connection")])
								: __("Tool")}
						</div>
					</div>
				</div>
				<span className="faip-execution-card-status">{statusLabel}</span>
			</div>
			{execution.input_summary?.length ? (
				<div className="faip-execution-card-grid">
					{execution.input_summary.map((row: any) => (
						<div key={row.label} className="faip-execution-pill">
							<span>{row.label}</span>
							<strong>{row.value}</strong>
						</div>
					))}
				</div>
			) : null}
			{execution.result_summary ? (
				<div className="faip-execution-card-summary">{execution.result_summary}</div>
			) : null}
		</button>
	);
}
