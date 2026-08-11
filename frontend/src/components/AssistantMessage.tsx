import React, { useMemo } from "react";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { ActivityGroup } from "./ActivityGroup";
import { ConfirmCard } from "./ConfirmCard";
import { FeedbackBar } from "./FeedbackBar";
import { useStore } from "../state/store";

export function AssistantMessage({ message }: { message: any }) {
	const { answerQuestion, toolApproval } = useStore();

	const questionByKey = useMemo(
		() => new Map((message.questions || []).map((question: any) => [question.key, question])),
		[message.questions]
	);

	const items = useMemo(() => {
		const output: any[] = [];
		for (const part of message.parts) {
			if (part.type !== "tool") {
				output.push({ kind: "text", id: part.id, part });
				continue;
			}
			const question = questionByKey.get(part.id);
			const approval = toolApproval[part.name] === true || question !== undefined || part.approval !== null;
			if (approval) {
				if (question && question._answer === undefined) {
					output.push({ kind: "confirm", id: part.id, part, question });
				} else {
					output.push({ kind: "approval", id: part.id, parts: [part] });
				}
				continue;
			}
			const last = output[output.length - 1];
			if (last?.kind === "activity") last.parts.push(part);
			else output.push({ kind: "activity", id: part.id, parts: [part] });
		}
		return output;
	}, [message.parts, questionByKey, toolApproval]);

	return (
		<div className="faip-assistant">
			{items.map((item: any) => {
				if (item.kind === "text") {
					return (
						<div key={item.id} className="faip-assistant-text faip-text-block">
							{item.part.text}
						</div>
					);
				}
				if (item.kind === "confirm") {
					return (
						<ConfirmCard
							key={item.id}
							question={item.question}
							tool={item.part}
							onAnswer={(answer) => answerQuestion(message.id, item.question.key, answer)}
						/>
					);
				}
				return <ActivityGroup key={item.id} parts={item.parts} live={message.pending} />;
			})}
			{message.pending && !message.parts.length ? (
				<div className="faip-working">
					<LoaderCircle size={15} className="faip-spin" />
					Thinking…
				</div>
			) : null}
			{message.runName && !message.pending && !message.questions?.length ? (
				<FeedbackBar runName={message.runName} feedback={message.feedback} />
			) : null}
			{message.error ? (
				<div className="faip-inline-error">
					<CircleAlert size={14} />
					{message.error}
				</div>
			) : null}
		</div>
	);
}
