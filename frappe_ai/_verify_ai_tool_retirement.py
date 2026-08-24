"""Temporary verification script for spec 010 Phase 1. Not part of the app; delete
after use."""

import frappe


def create_and_check():
    frappe.set_user("Administrator")

    if frappe.db.exists("AI MCP Connection", "Assistant Core"):
        doc = frappe.get_doc("AI MCP Connection", "Assistant Core")
    else:
        doc = frappe.get_doc(
            {
                "doctype": "AI MCP Connection",
                "connection_name": "Assistant Core",
                "connection_type": "streamable-http",
                "endpoint_url": "http://localhost:8000/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp",
                "enabled": 1,
                "api_key": frappe.local.conf.get("frappe_assistant_core_test_api_key"),
                "api_secret": frappe.local.conf.get("frappe_assistant_core_test_api_secret"),
            }
        )
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

    from frappe_ai.api.mcp import check_connection

    result = check_connection(doc.name)
    frappe.db.commit()
    print("RESULT:", result)
    return result
