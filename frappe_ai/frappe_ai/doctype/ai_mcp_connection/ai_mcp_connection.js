frappe.ui.form.on("AI MCP Connection", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Test Connection"), () => {
			(frm.is_dirty() ? frm.save() : Promise.resolve())
				.then(() =>
					frappe.call({
						method: "frappe_ai.api.mcp.check_connection",
						args: {
							name: frm.doc.name,
						},
						freeze: true,
						freeze_message: __("Testing MCP connection..."),
					})
				)
				.then((response) => {
					const result = response.message || {};
					const indicator = result.is_connected ? "green" : "red";
					frappe.show_alert(
						{
							message: result.status_message || __("MCP connection check completed."),
							indicator,
						},
						8
					);
					return frm.reload_doc();
				});
		});
	},
});
