declare global {
	interface Window {
		__?: (message: string, args?: unknown[]) => string;
		frappe?: any;
	}

	// Desk globals provided by the host page. Declared here (not module-scoped)
	// so they are visible to every module without @types/jquery or @types/frappe.
	const frappe: any;
	const $: any;
}

export {};
