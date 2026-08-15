import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "../app";

function currentSessionFromUrl() {
	return new URL(window.location.href).searchParams.get("session");
}

function writeSessionToUrl(sessionName: string | null) {
	const url = new URL(window.location.href);
	if (sessionName) {
		url.searchParams.set("session", sessionName);
	} else {
		url.searchParams.delete("session");
	}
	window.history.replaceState({}, "", url.toString());
}

export function mountStandalonePage(container: HTMLElement) {
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
				variant="standalone"
				host={{
					getInitialSession: currentSessionFromUrl,
					onSessionChange: writeSessionToUrl,
				}}
			/>
		</div>
	);
}
