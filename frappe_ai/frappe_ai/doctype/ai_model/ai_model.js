// Copyright (c) 2026, Frappe Technologies and contributors
// License: MIT. See LICENSE

frappe.ui.form.on("AI Model", {
	refresh(frm) {
		load_provider_models(frm);

		if (frm.is_new()) return;

		frm.add_custom_button(__("Test Connection"), async () => {
			if (frm.is_dirty()) {
				frappe.msgprint({
					title: __("Unsaved Changes"),
					message: __("Save the document before testing the connection."),
					indicator: "orange",
				});
				return;
			}

			frappe.dom.freeze(__("Running model capability tests…"));
			try {
				const r = await frm.call("test_connection");
				show_connection_results(r && r.message ? r.message : {});
			} catch (error) {
				frappe.msgprint({
					title: __("Capability Test Failed"),
					message: escape_html(error && error.message ? error.message : __("The capability test could not be completed.")),
					indicator: "red",
				});
			} finally {
				frappe.dom.unfreeze();
			}
		});
	},

	provider(frm) {
		load_provider_models(frm);
	},
});

function show_connection_results(result) {
	const checks = Array.isArray(result.checks) ? result.checks : [];
	const rows = checks
		.map((check) => {
			const status = String(check.status || "unknown");
			const detail = check.code ? `${check.code}: ${check.message || ""}` : check.message || "";
			return `<tr><td><span class="ai-model-test-status ai-model-test-${escape_html(status)}">${escape_html(status)}</span></td><td>${escape_html(check.name || "check")}</td><td>${escape_html(detail)}</td></tr>`;
		})
		.join("");
	const warnings = Array.isArray(result.warnings) && result.warnings.length
		? `<p><strong>${__("Warnings")}</strong></p><ul>${result.warnings.map((warning) => `<li>${escape_html(warning)}</li>`).join("")}</ul>`
		: "";
	const passed = result.ok === true;
	frappe.msgprint({
		title: passed ? __("Model Capability Tests Passed") : __("Model Capability Tests Need Attention"),
		message: `<table class="table table-bordered"><thead><tr><th>${__("Status")}</th><th>${__("Check")}</th><th>${__("Details")}</th></tr></thead><tbody>${rows}</tbody></table>${warnings}`,
		indicator: passed ? "green" : "orange",
	});
}

function escape_html(value) {
	return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
		"&": "&amp;",
		"<": "&lt;",
		">": "&gt;",
		'"': "&quot;",
		"'": "&#39;",
	}[character]));
}

function load_provider_models(frm) {
	const field = frm.fields_dict.model_id;
	// Suggestions are hints, never a restriction — allow any typed model_id.
	field.df.ignore_validation = true;
	if (!frm.doc.provider) {
		field.set_data([]);
		return;
	}
	frappe.call({
		method: "frappe_ai.frappe_ai.doctype.ai_model.ai_model.get_provider_models",
		args: { provider: frm.doc.provider },
		callback: ({ message }) => field.set_data(message || []),
	});
}
