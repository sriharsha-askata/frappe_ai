frappe.ui.form.on("AI Agent MCP Connection", {
	mcp_connection(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.mcp_connection) {
			frappe.model.set_value(cdt, cdn, "available_tools", "[]");
			frappe.model.set_value(cdt, cdn, "include_tools", "[]");
			return;
		}

		frappe.call({
			method: "frappe_ai.api.mcp.get_mcp_connection_tools",
			args: { name: row.mcp_connection },
			freeze: true,
			freeze_message: __("Fetching MCP tools..."),
		}).then((response) => {
			const tools = response.message || [];
			frappe.model.set_value(
				cdt,
				cdn,
				"available_tools",
				JSON.stringify(tools.map((tool) => ({ name: tool.name, description: tool.description })))
			);
			frappe.model.set_value(cdt, cdn, "include_tools", JSON.stringify(tools.map((tool) => tool.name)));
			frappe.show_alert({ message: __("Fetched {0} MCP tools.", [tools.length]), indicator: "green" });
		});
	},
});
