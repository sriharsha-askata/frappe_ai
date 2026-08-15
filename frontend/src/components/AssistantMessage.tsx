import React from "react";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { ConfirmCard } from "./ConfirmCard";
import { ExecutionCard } from "./ExecutionCard";
import { FeedbackBar } from "./FeedbackBar";
import { useStore } from "../state/store";

export function AssistantMessage({ message }: { message: any }) {
	const { answerQuestion } = useStore();

	return (
		<div className="faip-assistant">
			{message.content ? <div className="faip-assistant-text faip-text-block">{message.content}</div> : null}
			{message.executions.map((execution: any) => {
				const question = message.questions.find((item: any) => item.key === execution.id && item._answer === undefined);
				if (question) {
					return (
						<ConfirmCard
							key={execution.id}
							question={question}
							tool={{ name: execution.tool_name, arguments: execution.raw_input }}
							onAnswer={(answer) => answerQuestion(message.id, question.key, answer)}
						/>
					);
				}
				return <ExecutionCard key={execution.id} execution={execution} message={message} />;
			})}
			{message.pending && !message.content && !message.executions.length ? (
				<div className="faip-working">
					<LoaderCircle size={15} className="faip-spin" />
					Thinking…
				</div>
			) : null}
			{message.run && !message.pending && !message.questions?.length ? (
				<FeedbackBar runName={message.run} feedback={message.feedback} />
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
