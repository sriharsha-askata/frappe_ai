import React from "react";
import { CheckCircle2, CircleDashed, CircleX, Wrench } from "lucide-react";
import { parseArgs, toolContext, toolError, toolLabel } from "../lib/toolMeta";

type ToolPart = {
	id: string;
	type: "tool";
	name: string;
	arguments?: any;
	result: string | null;
	approval: "approved" | "denied" | "redirected" | null;
};

function renderResult(result: string | null) {
	if (!result) return null;
	try {
		return JSON.stringify(JSON.parse(result), null, 2);
	} catch {
		return result;
	}
}

export function ActivityGroup({
	parts,
	live,
}: {
	parts: ToolPart[];
	live: boolean;
}) {
	return (
		<div className="faip-activity-group">
			{parts.map((part) => {
				const context = toolContext(part.arguments);
				const error = toolError(part.result);
				const args = parseArgs(part.arguments);
				const statusIcon = error ? (
					<CircleX size={15} />
				) : part.result ? (
					<CheckCircle2 size={15} />
				) : live ? (
					<CircleDashed size={15} className="faip-spin-slow" />
				) : (
					<Wrench size={15} />
				);
				return (
					<div key={part.id} className={`faip-activity ${error ? "is-error" : ""}`}>
						<div className="faip-activity-header">
							<span className="faip-activity-icon">{statusIcon}</span>
							<div>
								<div className="faip-activity-title">{toolLabel(part.name)}</div>
								{context ? <div className="faip-activity-meta">{context}</div> : null}
							</div>
						</div>
						{Object.keys(args).length ? (
							<pre className="faip-code-block">{JSON.stringify(args, null, 2)}</pre>
						) : null}
						{part.result ? <pre className="faip-code-block">{renderResult(part.result)}</pre> : null}
					</div>
				);
			})}
		</div>
	);
}
