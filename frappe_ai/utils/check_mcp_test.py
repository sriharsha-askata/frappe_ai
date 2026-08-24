"""Temporary test script for MCP connection check."""
import frappe


def run():
    try:
        conn = frappe.get_doc("AI MCP Connection", "mock-mcp-server")
        frappe.msgprint(f"Connection: {conn.connection_name} ({conn.connection_type})")
        
        result = frappe.get_attr("frappe_ai.api.mcp.check_connection")(conn.name)
        
        output_lines = []
        output_lines.append(f"is_connected: {result.get('is_connected')}")
        output_lines.append(f"status_message: {result.get('status_message')}")
        
        tools = result.get('tools', [])
        output_lines.append(f"tools count: {len(tools)}")
        
        if tools:
            output_lines.append("Discovered tools:")
            for tool in tools:
                output_lines.append(f"  - {tool['tool_name']}: {(tool.get('description') or '')[:50]}")
        
        frappe.db.commit()
        output_lines.append("Done")
        
        with open("/tmp/mcp_check_result.txt", "w") as f:
            f.write("\n".join(output_lines))
        
        print("Written to /tmp/mcp_check_result.txt")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        with open("/tmp/mcp_check_result.txt", "w") as f:
            f.write(f"ERROR: {e}\n\n{tb}")
        print("Error written to /tmp/mcp_check_result.txt")
