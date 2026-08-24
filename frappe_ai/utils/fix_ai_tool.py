"""Force re-sync AI Tool DocType from JSON."""
import frappe
import json


def run():
    # Delete the existing DocType record
    frappe.db.sql("DELETE FROM `tabDocField` WHERE parent='AI Tool'")
    frappe.db.sql("DELETE FROM `tabDocType` WHERE name='AI Tool'")
    frappe.db.commit()
    
    # Reload from JSON
    frappe.clear_cache()
    
    # Re-create from JSON definition
    with open("/home/a/harsha/harsha/apps/frappe_ai/frappe_ai/frappe_ai/doctype/ai_tool/ai_tool.json") as f:
        dt_json = json.load(f)
    
    dt = frappe.get_doc(dt_json)
    dt.insert(ignore_permissions=True)
    frappe.db.commit()
    
    # Verify
    dt2 = frappe.get_doc("DocType", "AI Tool")
    print("Fields synced: " + str(len(dt2.fields)))
    for f in dt2.fields[:5]:
        print("  - " + f.fieldname + ": " + f.fieldtype)
