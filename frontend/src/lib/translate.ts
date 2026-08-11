export function __(message: string, args?: unknown[]) {
	if (typeof window !== "undefined" && typeof window.__ === "function") {
		return window.__(message, args);
	}
	return message;
}
