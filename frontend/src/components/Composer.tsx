import React, { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Paperclip, Square } from "lucide-react";
import { AttachmentChip } from "./AttachmentChip";
import { useStore } from "../state/store";
import { __ } from "../lib/translate";

export function Composer({ onHeight }: { onHeight?: (height: number) => void }) {
	const {
		agents,
		models,
		selectedAgent,
		selectedModel,
		attachments,
		supportedFileTypes,
		sending,
		paused,
		locked,
		loaded,
		needsSetup,
		uploading,
		focusTick,
		agentLabel,
		modelLabel,
		setAgent,
		setModel,
		send,
		stopRun,
		attachFiles,
		removeAttachment,
	} = useStore();

	const [text, setText] = useState("");
	const [dragging, setDragging] = useState(false);
	const textareaRef = useRef<HTMLTextAreaElement | null>(null);
	const rootRef = useRef<HTMLDivElement | null>(null);
	const fileInputRef = useRef<HTMLInputElement | null>(null);

	const accept = useMemo(
		() => (supportedFileTypes || []).map((ext: string) => `.${ext}`).join(","),
		[supportedFileTypes]
	);
	const inputDisabled = !loaded || sending || paused || needsSetup;
	const canSend = text.trim() && !inputDisabled && !uploading;
	const placeholder = !loaded
		? __("Loading…")
		: needsSetup
			? __("Setup required…")
			: __("Ask {0}…", [agentLabel(selectedAgent)]);

	useEffect(() => {
		const observer = new ResizeObserver((entries) => {
			onHeight?.(entries[0].target.clientHeight);
		});
		if (rootRef.current) observer.observe(rootRef.current);
		return () => observer.disconnect();
	}, [onHeight]);

	useEffect(() => {
		textareaRef.current?.focus();
	}, [focusTick]);

	function resize() {
		const textarea = textareaRef.current;
		if (!textarea) return;
		textarea.style.height = "auto";
		textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
	}

	function submit() {
		if (!canSend) return;
		void send(text);
		setText("");
		requestAnimationFrame(resize);
	}

	return (
		<div
			ref={rootRef}
			className={`faip-composer ${dragging ? "is-dragging" : ""}`}
			onDragOver={(event) => {
				if (inputDisabled) return;
				event.preventDefault();
				setDragging(true);
			}}
			onDragLeave={(event) => {
				if (event.currentTarget.contains(event.relatedTarget as Node)) return;
				setDragging(false);
			}}
			onDrop={(event) => {
				event.preventDefault();
				setDragging(false);
				if (inputDisabled) return;
				attachFiles(event.dataTransfer.files);
			}}
		>
			{attachments.length ? (
				<div className="faip-composer-attachments">
					{attachments.map((attachment) => (
						<AttachmentChip
							key={attachment.uid}
							fileName={attachment.file_name}
							fileSize={attachment.file_size}
							status={attachment.status}
							error={attachment.error}
							removable
							onRemove={() => removeAttachment(attachment.uid)}
						/>
					))}
				</div>
			) : null}

			<textarea
				ref={textareaRef}
				rows={1}
				value={text}
				disabled={inputDisabled}
				placeholder={placeholder}
				className="faip-composer-input"
				onInput={resize}
				onChange={(event) => setText(event.target.value)}
				onKeyDown={(event) => {
					if (event.key === "Enter" && !event.shiftKey) {
						event.preventDefault();
						submit();
					}
				}}
			/>

			<div className="faip-composer-footer">
				<div className="faip-composer-tools">
					<button type="button" className="faip-icon-button" disabled={inputDisabled} onClick={() => fileInputRef.current?.click()}>
						<Paperclip size={15} />
					</button>
					<input
						ref={fileInputRef}
						type="file"
						multiple
						accept={accept}
						hidden
						onChange={(event) => {
							attachFiles(event.target.files);
							event.target.value = "";
						}}
					/>
					<select className="faip-select" disabled={locked} value={selectedAgent || ""} onChange={(event) => setAgent(event.target.value)}>
						{agents.map((agent) => (
							<option key={agent.name} value={agent.name}>
								{agent.title || agent.name}
							</option>
						))}
					</select>
					<select className="faip-select" value={selectedModel || ""} onChange={(event) => setModel(event.target.value || null)}>
						<option value="">{__("Default")}</option>
						{models.map((model) => (
							<option key={model.name} value={model.name}>
								{modelLabel(model.name) || model.name}
							</option>
						))}
					</select>
				</div>

				{sending ? (
					<button type="button" className="faip-danger-button" onClick={stopRun}>
						<Square size={14} />
						{__("Stop")}
					</button>
				) : (
					<button type="button" className="faip-primary-button" disabled={!canSend} onClick={submit}>
						<ArrowUp size={14} />
						{__("Send")}
					</button>
				)}
			</div>
		</div>
	);
}
