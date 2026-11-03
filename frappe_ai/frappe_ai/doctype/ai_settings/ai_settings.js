// Copyright (c) 2026, Frappe Technologies and contributors
// License: MIT. See LICENSE

frappe.ui.form.on("AI Settings", {
	setup(frm) {
		// Only enabled models can transcribe or speak.
		for (const field of ["transcription_model", "speech_model"]) {
			frm.set_query(field, () => ({ filters: { enabled: 1 } }));
		}
	},
});
