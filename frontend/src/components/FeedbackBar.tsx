import React, { useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

export function FeedbackBar({
	runName,
	feedback,
}: {
	runName: string;
	feedback: { rating: string; comment: string } | null;
}) {
	const { submitFeedback } = useStore();
	const [comment, setComment] = useState(feedback?.comment || "");
	const [busy, setBusy] = useState(false);

	async function rate(rating: string) {
		setBusy(true);
		try {
			await submitFeedback(runName, rating, rating === "Down" ? comment : "");
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="faip-feedback">
			<button
				type="button"
				className={`faip-feedback-button ${feedback?.rating === "Up" ? "is-active" : ""}`}
				disabled={busy}
				onClick={() => rate(feedback?.rating === "Up" ? "None" : "Up")}
			>
				<ThumbsUp size={14} />
				{__("Helpful")}
			</button>
			<button
				type="button"
				className={`faip-feedback-button ${feedback?.rating === "Down" ? "is-active" : ""}`}
				disabled={busy}
				onClick={() => rate(feedback?.rating === "Down" ? "None" : "Down")}
			>
				<ThumbsDown size={14} />
				{__("Needs work")}
			</button>
			{feedback?.rating === "Down" ? (
				<textarea
					className="faip-textarea"
					placeholder={__("Optional feedback for the agent")}
					value={comment}
					onChange={(event) => setComment(event.target.value)}
					onBlur={() => void submitFeedback(runName, "Down", comment)}
				/>
			) : null}
		</div>
	);
}
