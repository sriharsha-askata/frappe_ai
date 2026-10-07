"""Remove legacy DocType metadata after the agent and MCP fields are synced.

Retirement machinery: remove in Stage 2 with its patches.txt entry.
"""

import frappe


def execute():
	for name in ("AI Agent Tool Config", "AI Agent Tool", "AI Tool"):
		if frappe.db.exists("DocType", name):
			frappe.delete_doc("DocType", name, force=True, ignore_permissions=True)
