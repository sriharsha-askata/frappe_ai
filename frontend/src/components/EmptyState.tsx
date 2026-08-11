import React from "react";
import { Bot, Database, Hammer, WandSparkles } from "lucide-react";
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

	const prompts = [
		__("Summarize the latest activity in this site."),
		__("Help me draft a new workflow or automation."),
		__("Find the records I should review today."),
	];

	return (
		<div className="faip-empty">
			<div className="faip-empty-mark">
				<Bot size={26} />
			</div>
			{setup ? (
				<>
					<h3>{__("Finish setup to start")}</h3>
					<p>{__("The assistant needs a few things configured first:")}</p>
					<ol className="faip-empty-list">
						{!hasModels ? (
							<li>{__("Create and enable an AI Model with your provider credentials.")}</li>
						) : null}
						{!hasAgents ? <li>{__("Enable an AI Agent after at least one model exists.")}</li> : null}
						<li>{__("Enable Server Scripts in your site config so the assistant can run code.")}</li>
					</ol>
				</>
			) : (
				<>
					<h3>{__("A workspace for data, workflows, and action")}</h3>
					<p>{__("Search records, draft changes, inspect tool activity, and keep the whole AI flow in one place.")}</p>
					<div className="faip-empty-feature-grid">
						<div className="faip-empty-feature">
							<Database size={16} />
							<span>{__("Ask about live ERP data")}</span>
						</div>
						<div className="faip-empty-feature">
							<Hammer size={16} />
							<span>{__("Run guided tool actions")}</span>
						</div>
						<div className="faip-empty-feature">
							<WandSparkles size={16} />
							<span>{__("Draft and iterate faster")}</span>
						</div>
					</div>
					<div className="faip-empty-prompts">
						{prompts.map((prompt) => (
							<button
								key={prompt}
								type="button"
								className="faip-prompt-card"
								onClick={() => void send(prompt)}
							>
								{prompt}
							</button>
						))}
					</div>
				</>
			)}
		</div>
	);
}
