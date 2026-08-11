import React, { useMemo, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { confirmTitle } from "../lib/toolMeta";
import { __ } from "../lib/translate";

export function ConfirmCard({
	question,
	tool,
	onAnswer,
}: {
	question: any;
	tool: any;
	onAnswer: (answer: string) => void;
}) {
	const [mode, setMode] = useState<"approve" | "deny" | "redirect">("approve");
	const [text, setText] = useState("");
	const meta = useMemo(() => confirmTitle(tool.name, tool.arguments), [tool.arguments, tool.name]);

	return (
		<div className={`faip-confirm-card ${meta.danger ? "is-danger" : ""}`}>
			<div className="faip-confirm-header">
				<ShieldAlert size={16} />
				<div>
					<div className="faip-confirm-title">{meta.title}</div>
					<div className="faip-confirm-prompt">{question.prompt}</div>
				</div>
			</div>
			{tool.arguments ? (
				<pre className="faip-code-block">{JSON.stringify(tool.arguments, null, 2)}</pre>
			) : null}
			<div className="faip-confirm-actions">
				<button type="button" className="faip-secondary-button" onClick={() => onAnswer("Approve")}>
					{__("Approve")}
				</button>
				<button type="button" className="faip-secondary-button" onClick={() => onAnswer("Deny")}>
					{__("Deny")}
				</button>
				<button type="button" className="faip-secondary-button" onClick={() => setMode("redirect")}>
					{__("Request changes")}
				</button>
			</div>
			{mode === "redirect" ? (
				<div className="faip-confirm-redirect">
					<textarea
						className="faip-textarea"
						placeholder={__("Tell the agent what to change")}
						value={text}
						onChange={(event) => setText(event.target.value)}
					/>
					<button type="button" className="faip-primary-button" onClick={() => onAnswer(text)}>
						{__("Send feedback")}
					</button>
				</div>
			) : null}
		</div>
	);
}
