import React from "react";
import { CircleAlert, LoaderCircle, Paperclip, X } from "lucide-react";

function formatSize(size?: number) {
	if (!size) return "";
	if (size < 1024) return `${size} B`;
	if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
	return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function AttachmentChip({
	fileName,
	fileSize,
	status,
	error,
	removable = false,
	onRemove,
}: {
	fileName: string;
	fileSize?: number;
	status?: "uploading" | "ready" | "error";
	error?: string;
	removable?: boolean;
	onRemove?: () => void;
}) {
	return (
		<div className={`faip-chip ${status === "error" ? "is-error" : ""}`}>
			<span className="faip-chip-icon">
				{status === "uploading" ? (
					<LoaderCircle size={14} className="faip-spin" />
				) : status === "error" ? (
					<CircleAlert size={14} />
				) : (
					<Paperclip size={14} />
				)}
			</span>
			<span className="faip-chip-label" title={fileName}>
				{fileName}
			</span>
			{fileSize ? <span className="faip-chip-size">{formatSize(fileSize)}</span> : null}
			{error ? <span className="faip-chip-error">{error}</span> : null}
			{removable ? (
				<button type="button" className="faip-icon-button" onClick={onRemove} aria-label="Remove">
					<X size={14} />
				</button>
			) : null}
		</div>
	);
}
