# MCP Integration Setup Guide

> A practical guide to connecting frappe_ai with MCP servers (Assistant Core or custom)

---

## Quick Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                      frappe_ai (AI Agent)                         │
│                                                                    │
│   AI Agent ──────────────────────────────────────────┐             │
│                                                     │             │
│   Tools: get_document, list_documents, search...   │             │
│         (comes from MCP Connection)                │             │
│                                                     │             │
└─────────────────────────────────────────────────────┘             │
                              │                                      │
                              │ MCP Connection                       │
                              ▼                                      │
┌────────────────────────────────────────────────────────────────────┐
│                    MCP Server                                      │
│                                                                    │
│   Endpoint: /api/method/...handle_mcp                            │
│                                                                    │
│   Tools from:                                                      │
│   • frappe_assistant_core (built-in Frappe tools)                │
│   • factory_automation_api (custom business tools)                │
│   • tender_automation (custom business tools)                    │
│   • Any other app...                                            │
└────────────────────────────────────────────────────────────────────┘
```

---

## Two Ways to Add Tools to MCP

### Option 1: Use Assistant Core's Built-in Tools

Assistant Core already provides tools that work with any DocType:

- `get_document` - Read a document
- `list_documents` - List documents
- `search_documents` - Search
- `create_document` - Create new document
- `update_document` - Update document
- `delete_document` - Delete document
- `run_workflow` - Trigger workflow
- And more...

**No additional code needed** - just connect!

### Option 2: Create Custom Tools

If you need specific business logic, create a tool class:

```python
# In your app: my_app/utils/my_tool.py

from frappe_assistant_core.core.base_tool import BaseTool

class MyCustomTool(BaseTool):
    def __init__(self):
        self.name = "my_custom_tool"
        self.description = "Does something specific for my business"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Input parameter"}
            },
            "required": ["input"]
        }
    
    def execute(self, arguments):
        # Your business logic here
        return {"result": "Done!"}

# Register in hooks.py
def get_app_utils():
    return {
        "assistant_tools": [
            "my_app.utils.my_tool.MyCustomTool"
        ]
    }
```

---

## Step-by-Step: Connect to Assistant Core

### Step 1: Generate API Key for User

1. Go to: **Users > [Select User] > API Access**
2. Click **Generate API Key**
3. Save the **API Key** and **API Secret**

### Step 2: Enable Assistant for User

1. Go to: **Users > [Select User]**
2. Check **Enable Assistant** field
3. Save

### Step 3: Create AI MCP Connection

1. Go to: **AI MCP Connection** (DocType)
2. Create new:
   ```
   Connection Name: Assistant Core
   Connection Type: streamable-http
   Endpoint URL: https://yoursite.com/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp
   API Key: <your_user_api_key>
   API Secret: <your_user_api_secret>
   ```

3. Save

### Step 4: Test Connection

1. Click **Check Connection** button
2. Should show "Connected (X tools)"
3. If error, check API key/secret

### Step 5: Attach to AI Agent

1. Go to: **AI Agent**
2. Open your agent
3. In **MCP Connections** child table:
   - Add row
   - Select your connection
   - Optionally specify `include_tools` to limit tools

### Step 6: Test the Agent

Run your agent - it should now have access to all Assistant Core tools!

---

## Connecting Custom MCP Servers

If you have a separate MCP server (like tender_automation's MCP):

### Option A: stdio Connection

For local processes:

```
Connection Type: stdio
Command: python /path/to/mcp_server.py
Command Args: ["--transport", "stdio"]
Environment Variables: {}
```

### Option B: SSE Connection

For HTTP endpoints:

```
Connection Type: SSE
Endpoint URL: https://yourserver.com/mcp
Environment Variables: {}
```

---

## Authentication Methods

### For Assistant Core

| Header | Format | Example |
|--------|--------|---------|
| API Key/Secret | `token api_key:api_secret` | `token abc123:xyz789` |
| OAuth Bearer | `Bearer <token>` | `Bearer eyJhbGci...` |

### How It Works in Code

```python
# In builder.py - MCP connection handling
if connection_type == "streamable-http":
    headers = {}
    if api_key and api_secret:
        headers["Authorization"] = f"token {api_key}:{api_secret}"
    
    tools.append(MCPTools(
        url=endpoint_url,
        transport="streamable-http",
        headers=headers,
        include_tools=include_tools
    ))
```

---

## Troubleshooting

### Connection Fails

1. **Check API Key**: Make sure it's valid and not expired
2. **Check Permissions**: User must have "Assistant Enabled"
3. **Check Endpoint URL**: Must be accessible from the server
4. **Check Site URL**: Ensure proper URL format

### Tools Not Showing

1. Click **Check Connection** to refresh tool discovery
2. Check `include_tools` - may be limiting tools
3. Verify user has permissions for those tools in Assistant Core

### Authentication Errors

1. Verify API Key/Secret is correct
2. Check user has "Assistant Enabled" in User doctype
3. For OAuth - ensure token is valid and not expired

---

## Quick Reference

### AI MCP Connection Fields

| Field | Required | Description |
|-------|----------|-------------|
| connection_name | Yes | Unique name |
| connection_type | Yes | stdio, SSE, or streamable-http |
| endpoint_url | For HTTP | MCP server URL |
| command | For stdio | Command to run |
| api_key | Optional | API key for auth |
| api_secret | Optional | API secret for auth |
| include_tools | Optional | Limit which tools are available |

### Connection Types

- **stdio**: Local process (good for development)
- **SSE**: HTTP long-polling (older standard)
- **streamable-http**: HTTP streaming (recommended for production)

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                     Your Frappe Site                             │
│                                                                  │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│   │   frappe_ai │    │  Assistant  │    │    Your    │       │
│   │  (AI Agent) │◄──►│    Core     │◄──►│    Apps    │       │
│   │             │    │ (MCP Server)│    │  (Tools)   │       │
│   └─────────────┘    └─────────────┘    └─────────────┘       │
│          │                   │                  │                 │
│          │      Tools:       │    Register     │                 │
│          │  get_document    │◄── via hooks ───┘                 │
│          │  list_documents │                                  │
│          │  search...     │                                  │
│          └─────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Next Steps

1. **Start Simple**: Connect to Assistant Core first
2. **Test Read-Only**: Verify tools work with read operations
3. **Add Custom Tools**: Create tools in your apps as needed
4. **Explore**: Try different tools and see what your agent can do!
