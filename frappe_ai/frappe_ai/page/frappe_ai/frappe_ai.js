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

	const assetVersion = Date.now();

	const ensureAssets = () =>
		new Promise((resolve, reject) => {
			const cssHref = `/assets/frappe_ai/frappe_ai_panel/frappe_ai_panel.css?v=${assetVersion}`;
			const jsSrc = `/assets/frappe_ai/frappe_ai_panel/frappe_ai_panel.js?v=${assetVersion}`;

			if (
				!Array.from(document.querySelectorAll('link[rel="stylesheet"]')).find((link) =>
					(link.getAttribute("href") || "").includes("frappe_ai_panel.css")
				)
			) {
				const link = document.createElement("link");
				link.rel = "stylesheet";
				link.href = cssHref;
				document.head.appendChild(link);
			}

			const existingScript = Array.from(document.querySelectorAll("script")).find((script) =>
				(script.getAttribute("src") || "").includes("frappe_ai_panel.js")
			);
			if (existingScript) {
				existingScript.remove();
			}

			const script = document.createElement("script");
			script.src = jsSrc;
			script.async = true;
			script.onload = resolve;
			script.onerror = reject;
			document.body.appendChild(script);
		});

	const mount = async () => {
		try {
			await ensureAssets();
		} catch (error) {
			host.innerHTML = `<div class="text-muted" style="padding: 1rem;">${__("Frappe AI assets failed to load.")}</div>`;
			return;
		}

		if (frappe?.frappe_ai?.mountPage) {
			frappe.frappe_ai.mountPage(host);
		} else {
			host.innerHTML = `<div class="text-muted" style="padding: 1rem;">${__("Frappe AI assets are still loading. Refresh this page.")}</div>`;
		}
	};

	void mount();
	$(wrapper).bind("show", mount);
};
