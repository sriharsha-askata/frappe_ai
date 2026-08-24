"""E2E test: Check MCP connection and verify tools discovery."""
import frappe
import json


def run():
    results = []

    # Step 1: Verify AI Tool doctype
    ai_tool_exists = frappe.db.exists("DocType", "AI Tool")
    results.append(f"AI Tool DocType exists: {bool(ai_tool_exists)}")

    # Step 2: Check AI Tool fields
    if ai_tool_exists:
        dt = frappe.get_doc("DocType", "AI Tool")
        field_names = [f.fieldname for f in dt.fields]
        results.append(f"AI Tool fields: {field_names}")

    # Step 3: Check MCP connection
    conn_exists = frappe.db.exists("AI MCP Connection", "mock-mcp-server")
    results.append(f"MCP Connection exists: {bool(conn_exists)}")

    if not conn_exists:
        results.append("ERROR: No MCP connection found")
        _write_results(results)
        return

    # Step 4: Call check_connection to discover tools
    try:
        from frappe_ai.api.mcp import check_connection
        conn = frappe.get_doc("AI MCP Connection", "mock-mcp-server")
        result = check_connection(conn.name)
        results.append(f"is_connected: {result.get('is_connected')}")
        results.append(f"status: {result.get('status_message')}")

        tools = result.get("tools", [])
        results.append(f"tools found: {len(tools)}")
        for t in tools:
            results.append(f"  - {t['tool_name']}: {(t.get('description') or '')[:50]}")
    except Exception as e:
        import traceback
        results.append(f"ERROR during check_connection: {e}")
        results.append(traceback.format_exc())

    # Step 5: Verify MCP connection tools were saved
    try:
        conn.reload()
        saved_tools = conn.tools or []
        results.append(f"\nMCP Connection saved tools: {len(saved_tools)}")
        for st in saved_tools:
            results.append(f"  - {st.tool_name}: available={st.available}")
    except Exception as e:
        results.append(f"ERROR reading saved tools: {e}")

    frappe.db.commit()
    results.append("\nDone!")
    _write_results(results)


def _write_results(results):
    with open("/tmp/mcp_e2e_result.txt", "w") as f:
        f.write("\n".join(results))
