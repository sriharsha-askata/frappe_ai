// Copyright (c) 2026, Frappe Technologies and contributors
// License: MIT. See LICENSE

frappe.ui.form.on("AI Settings", {
	setup(frm) {
		// Only enabled models can transcribe audio.
		frm.set_query("transcription_model", () => ({ filters: { enabled: 1 } }));
	},
});
