import { mountDeskPanel } from "./hosts/deskPanel";
import { mountStandalonePage } from "./hosts/frappePage";
import "./styles/panel.css";

if (typeof $ !== "undefined") {
	$(document).on("app_ready", mountDeskPanel);
} else {
	document.addEventListener("DOMContentLoaded", mountDeskPanel);
}

if (typeof frappe !== "undefined") {
	frappe.provide("frappe.frappe_ai");
	frappe.frappe_ai.mountStandalonePage = mountStandalonePage;
}
