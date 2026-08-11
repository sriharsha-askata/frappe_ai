import React, { useEffect, useRef } from "react";
import { CircleAlert } from "lucide-react";
import { AssistantMessage } from "./AssistantMessage";
import { EmptyState } from "./EmptyState";
import { UserMessage } from "./UserMessage";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

export function MessageList() {
	const { messages, needsSetup, agents, models, scrollTick, forceScroll, clearForceScroll } = useStore();
	const ref = useRef<HTMLDivElement | null>(null);
	const stickRef = useRef(true);

	useEffect(() => {
		const element = ref.current;
		if (!element) return;
		if (stickRef.current || forceScroll) {
			element.scrollTo({ top: element.scrollHeight, behavior: forceScroll ? "smooth" : "auto" });
			stickRef.current = true;
			if (forceScroll) clearForceScroll();
		}
	}, [clearForceScroll, forceScroll, scrollTick]);

	return (
		<div
			ref={ref}
			className="faip-message-list"
			onScroll={() => {
				const element = ref.current;
				if (!element) return;
				stickRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
			}}
		>
			<div className="faip-message-stack">
				{!messages.length ? (
					<EmptyState
						setup={needsSetup}
						hasModels={models.length > 0}
						hasAgents={agents.length > 0}
					/>
				) : null}
				{messages.map((message) =>
					message.role === "user" ? (
						<React.Fragment key={message.id}>
							<UserMessage content={message.content} attachments={message.attachments} />
							{message.interrupted ? (
								<div className="faip-inline-error">
									<CircleAlert size={14} />
									{__("Response interrupted")}
								</div>
							) : null}
						</React.Fragment>
					) : (
						<AssistantMessage key={message.id} message={message} />
					)
				)}
			</div>
		</div>
	);
}
