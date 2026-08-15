import React from "react";
import { Compass, Database, Hammer, Sparkles } from "lucide-react";
import { __ } from "../lib/translate";
import { useStore } from "../state/store";

export function EmptyState({
	setup,
	hasModels,
	hasAgents,
}: {
	setup: boolean;
	hasModels: boolean;
	hasAgents: boolean;
}) {
	const { send } = useStore();
	const prompts = [__("Search records"), __("Run a tool"), __("Analyze data")];

	return (
		<div className="faip-empty">
			<div className="faip-empty-mark">
				<Compass size={26} />
			</div>
			{setup ? (
				<>
					<h3>{__("Finish setup to start")}</h3>
					<p>{__("The workspace needs core AI configuration before it can run.")}</p>
					<ol className="faip-empty-list">
						{!hasModels ? <li>{__("Create and enable an AI Model.")}</li> : null}
						{!hasAgents ? <li>{__("Enable at least one AI Agent.")}</li> : null}
						<li>{__("Enable Server Scripts so tool execution can run safely.")}</li>
					</ol>
				</>
			) : (
				<>
					<h3>{__("Start in the workspace")}</h3>
					<p>{__("Use a prompt, then inspect tools, MCP activity, and execution details without leaving the transcript.")}</p>
					<div className="faip-empty-feature-grid">
						<div className="faip-empty-feature">
							<Database size={16} />
							<span>{__("Search live records")}</span>
						</div>
						<div className="faip-empty-feature">
							<Hammer size={16} />
							<span>{__("Run guided actions")}</span>
						</div>
						<div className="faip-empty-feature">
							<Sparkles size={16} />
							<span>{__("Inspect execution details")}</span>
						</div>
					</div>
					<div className="faip-empty-prompts">
						{prompts.map((prompt) => (
							<button key={prompt} type="button" className="faip-prompt-card" onClick={() => void send(prompt)}>
								{prompt}
							</button>
						))}
					</div>
				</>
			)}
		</div>
	);
}
