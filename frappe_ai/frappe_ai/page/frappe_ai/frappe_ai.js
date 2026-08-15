frappe.pages["frappe-ai"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Frappe AI"),
		single_column: true,
	});

	page.main.addClass("frappe-ai-page-shell");
	const host = document.createElement("div");
	host.className = "frappe-ai-page-root";
	page.body.empty().append(host);

	const mount = () => {
		if (frappe?.frappe_ai?.mountStandalonePage) {
			frappe.frappe_ai.mountStandalonePage(host);
			return;
		}
		host.innerHTML = `<div class="text-muted" style="padding: 1rem;">${__("Frappe AI assets are still loading. Refresh this page.")}</div>`;
	};

	mount();
	$(wrapper).bind("show", mount);
};
