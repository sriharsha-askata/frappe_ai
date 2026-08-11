import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app";
import { readPanelState, writePanelState } from "./lib/panelState";
import "./styles/panel.css";

const PANEL_WIDTH = 420;
const MIN_WIDTH = 360;

function DeskPanelHost() {
	const saved = useMemo(() => readPanelState(), []);
	const [open, setOpen] = useState(Boolean(saved.open));
	const [fullscreen, setFullscreen] = useState(saved.fullscreen ?? true);
	const [width, setWidth] = useState(saved.width || PANEL_WIDTH);
	const [sessionName, setSessionName] = useState<string | null>(saved.session || null);
	const shellRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		writePanelState({ open, fullscreen, width, session: sessionName });
	}, [fullscreen, open, sessionName, width]);

	useEffect(() => {
		const shell = shellRef.current;
		if (!shell) return;
		const applyTheme = () => {
			const theme = document.documentElement.getAttribute("data-theme") || "light";
			shell.setAttribute("data-theme", theme);
		};
		applyTheme();
		const observer = new MutationObserver(applyTheme);
		observer.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["data-theme"],
		});
		return () => observer.disconnect();
	}, []);

	useEffect(() => {
		const api = {
			show: () => setOpen(true),
			hide: () => setOpen(false),
			toggle: () => setOpen((value) => !value),
		};
		frappe.provide("frappe.frappe_ai");
		frappe.frappe_ai.panel = api;

		const toggle = () => setOpen((value) => !value);
		const shortcut = frappe?.ui?.keys?.add_shortcut;
		if (shortcut) {
			shortcut({
				shortcut: "ctrl+i",
				action: toggle,
				description: "Toggle Frappe AI panel",
				ignore_inputs: true,
			});
		} else {
			const onKeyDown = (event: KeyboardEvent) => {
				if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "i") {
					event.preventDefault();
					toggle();
				}
			};
			document.addEventListener("keydown", onKeyDown);
			return () => document.removeEventListener("keydown", onKeyDown);
		}
	}, []);

	useEffect(() => {
		const shell = shellRef.current;
		if (!shell) return;
		const handle = shell.querySelector(".faip-resize-handle") as HTMLDivElement | null;
		if (!handle) return;

		let previousTransition = "";
		const onMove = (event: MouseEvent) => {
			const max = window.innerWidth - 80;
			const next = Math.min(max, Math.max(MIN_WIDTH, window.innerWidth - event.clientX));
			setWidth(next);
			setFullscreen(false);
		};
		const onUp = () => {
			document.removeEventListener("mousemove", onMove);
			document.removeEventListener("mouseup", onUp);
			document.body.style.userSelect = "";
			if (shell) shell.style.transition = previousTransition;
		};
		const onDown = (event: MouseEvent) => {
			event.preventDefault();
			previousTransition = shell.style.transition;
			shell.style.transition = "none";
			document.body.style.userSelect = "none";
			document.addEventListener("mousemove", onMove);
			document.addEventListener("mouseup", onUp);
		};

		handle.addEventListener("mousedown", onDown);
		return () => handle.removeEventListener("mousedown", onDown);
	}, []);

	return (
		<div
			ref={shellRef}
			className="faip-shell"
			style={{
				width: fullscreen ? "100vw" : `${width}px`,
				transform: open ? "translateX(0)" : "translateX(100%)",
			}}
		>
			<div className="faip-resize-handle" />
			<App
				fullscreen={fullscreen}
				onClose={() => setOpen(false)}
				onToggleFullscreen={() => setFullscreen((value) => !value)}
				onSessionChange={setSessionName}
			/>
		</div>
	);
}

function mount() {
	const existing = document.getElementById("frappe-ai-root");
	const rootNode = existing || document.createElement("div");
	if ((rootNode as any).__faipMounted) return;
	rootNode.id = "frappe-ai-root";
	rootNode.classList.add("faip-root");
	if (!existing) document.body.appendChild(rootNode);
	(rootNode as any).__faipMounted = true;
	const root = createRoot(rootNode);
	root.render(<DeskPanelHost />);
}

function mountPage(container: HTMLElement) {
	let target = container;
	const existingRoot = (container as any).__faipPageRoot;
	if (!existingRoot && (container as any).__faipPageMounted) {
		const replacement = container.cloneNode(false) as HTMLElement;
		replacement.className = container.className;
		container.replaceWith(replacement);
		target = replacement;
	}

	(target as any).__faipPageMounted = true;
	const root = (target as any).__faipPageRoot || createRoot(target);
	(target as any).__faipPageRoot = root;
	root.render(
		<div className="faip-root faip-page-host">
			<App
				fullscreen
				onClose={() => {}}
				onToggleFullscreen={() => {}}
				onSessionChange={() => {}}
				mode="page"
			/>
		</div>
	);
}

if (typeof $ !== "undefined") {
	$(document).on("app_ready", mount);
} else {
	document.addEventListener("DOMContentLoaded", mount);
}

if (typeof frappe !== "undefined") {
	frappe.provide("frappe.frappe_ai");
	frappe.frappe_ai.mountPage = mountPage;
}
