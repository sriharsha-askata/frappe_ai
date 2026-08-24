# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import frappe
from frappe import _


@frappe.whitelist()
def sync_fac_tools():
    """Sync FAC tools from Assistant Core registry to AI FAC Tool doctype."""
    try:
        from frappe_assistant_core.core.tool_registry import get_tool_registry
    except ImportError:
        frappe.throw(_("frappe_assistant_core not installed"), title=_("Sync Error"))

    valid_categories = ["Core", "Custom", "Workflow", "Data", "Search", "Automation"]

    registry = get_tool_registry()
    tools_list = registry.get_available_tools()

    # tools_list can be list or dict - handle both
    if isinstance(tools_list, dict):
        tools_dict = tools_list
    else:
        tools_dict = {t.get("name"): t for t in tools_list if t.get("name")}

    for tool_name, tool_info in tools_dict.items():
        if frappe.db.exists("AI FAC Tool", tool_name):
            doc = frappe.get_doc("AI FAC Tool", tool_name)
        else:
            doc = frappe.get_doc({"doctype": "AI FAC Tool", "tool_name": tool_name})

        category = tool_info.get("category", "Core")
        # Map to valid category
        if category not in valid_categories:
            category = "Core"
        doc.category = category
        doc.description = tool_info.get("description", "")
        doc.enabled = tool_info.get("enabled", True)
        doc.save(ignore_permissions=True)

    frappe.db.commit()
    return {"synced": len(tools_dict), "tools": list(tools_dict.keys())}
