# How to Create Tools and Plugins for Assistant Core

> Complete guide to extending Frappe Assistant Core with custom tools and plugins.

---

## Quick Summary

| Approach | When to Use | Effort |
|----------|-------------|--------|
| **Tool only** | Simple tool, no complex setup | Low |
| **Plugin** | Multiple tools, need enable/disable, lifecycle hooks | Medium |
| **External App** | Tools in separate app, shared across installations | High |

---

## Part 1: Creating a Simple Tool (Quickest)

### Option A: Via Hooks (Recommended for External Apps)

This is the easiest way - just create a tool class and register it.

#### Step 1: Create Tool Class

```python
# your_app/utils/assistant_tools.py

from typing import Any, Dict
import frappe
from frappe_assistant_core.core.base_tool import BaseTool


class MyTool(BaseTool):
    """Custom tool for my app"""

    def __init__(self):
        super().__init__()
        self.name = "my_tool"
        self.description = "Does something useful"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "description": "DocType name"}
            },
            "required": ["doctype"]
        }

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        doctype = arguments.get("doctype")
        return {"success": True, "result": f"Processed {doctype}"}


# Required for discovery
my_tool = MyTool
```

#### Step 2: Register in hooks.py

```python
# your_app/hooks.py

def get_app_utils():
    """Register tools with Assistant Core"""
    return {
        "assistant_tools": [
            "your_app.utils.assistant_tools.MyTool"
        ]
    }
```

That's it! The tool is automatically discovered.

---

## Part 2: Creating a Full Plugin (Recommended for Feature Sets)

### Plugin Structure

```
your_app/
├── assistant_core/
│   ├── __init__.py
│   └── plugins/
│       └── your_plugin/
│           ├── __init__.py
│           ├── plugin.py          ← Plugin class
│           └── tools/
│               ├── __init__.py
│               ├── tool_one.py    ← Tool classes
│               └── tool_two.py
└── hooks.py                      ← Register plugin
```

### Step 1: Create Plugin Class

```python
# your_app/assistant_core/plugins/your_plugin/plugin.py

from typing import Any, Dict, List, Tuple, Optional

import frappe
from frappe_assistant_core.plugins.base_plugin import BasePlugin


class YourPlugin(BasePlugin):
    """Plugin for your custom functionality"""

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "your_plugin",
            "display_name": "Your Plugin",
            "description": "Description of what your plugin does",
            "version": "1.0.0",
            "author": "Your Name",
            "category": "Custom",  # e.g., Integration, Automation, Custom
            "dependencies": [],     # Python packages needed
            "requires_restart": False,
        }

    def get_tools(self) -> List[str]:
        """Return tool class names from tools/ directory"""
        return [
            "tool_one",
            "tool_two",
        ]

    def validate_environment(self) -> Tuple[bool, Optional[str]]:
        """Validate plugin can be enabled"""
        # Check dependencies
        can_enable, error = self._check_dependencies(self.get_info()["dependencies"])
        if not can_enable:
            return False, error
        
        # Check permissions
        can_enable, error = self._check_permissions(["Your DocType"])
        if not can_enable:
            return False, error
        
        return True, None

    def on_enable(self) -> None:
        """Called when plugin is enabled"""
        frappe.logger("your_plugin").info("Plugin enabled!")

    def on_disable(self) -> None:
        """Called when plugin is disabled"""
        frappe.logger("your_plugin").info("Plugin disabled!")
```

### Step 2: Create Tool Classes

```python
# your_app/assistant_core/plugins/your_plugin/tools/tool_one.py

from typing import Any, Dict
from frappe_assistant_core.core.base_tool import BaseTool


class ToolOne(BaseTool):
    """First tool in your plugin"""

    def __init__(self):
        super().__init__()
        self.name = "tool_one"
        self.description = "Does first thing"
        self.category = "Your Plugin"
        self.source_app = "your_app"
        
        self.inputSchema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Document name"}
            },
            "required": ["name"]
        }

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Your logic here
            return {"success": True, "result": "Done"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Required for discovery
tool_one = ToolOne
```

### Step 3: Register Plugin via Hook

In your app's `hooks.py`:

```python
# your_app/hooks.py

def get_app_utils():
    return {
        "assistant_plugins": [
            "your_app.assistant_core.plugins.your_plugin.plugin.YourPlugin"
        ]
    }
```

Or for a simpler approach, add tools directly:

```python
def get_app_utils():
    return {
        "assistant_tools": [
            "your_app.assistant_core.plugins.your_plugin.tools.tool_one.ToolOne",
            "your_app.assistant_core.plugins.your_plugin.tools.tool_two.ToolTwo",
        ]
    }
```

---

## Part 3: Enabling and Configuring

### Enable Plugin

1. Go to **Assistant Core Settings**
2. Find your plugin in the list
3. Enable it

### Configure Tools

Go to **FAC Tool Configuration** to:
- Enable/disable individual tools
- Set tool categories
- Configure role-based access

---

## Part 4: Plugin Lifecycle Hooks

```python
class YourPlugin(BasePlugin):
    
    def on_enable(self):
        """Called when enabled - setup resources"""
        # Create custom tables
        # Initialize services
        pass

    def on_disable(self):
        """Called when disabled - cleanup resources"""
        # Close connections
        # Clear caches
        pass

    def on_server_start(self):
        """Called when MCP server starts"""
        # Start background tasks
        # Warm up caches
        pass

    def on_server_stop(self):
        """Called when MCP server stops"""
        # Stop background tasks
        # Save state
        pass
```

---

## Part 5: Best Practices

### Tool Naming

```python
# Good - clear, specific names
self.name = "search_items_by_sku"
self.name = "get_production_order_status"
self.name = "calculate_material_shortage"

# Bad - too generic
self.name = "search"
self.name = "get_data"
```

### Descriptions

```python
# Good - tells AI when to use this tool
self.description = """
Search items by SKU or name. Use when user wants to find 
specific items or list items matching criteria.
Do NOT use for getting item details (use get_item instead).
"""

# Bad
self.description = "Search items"
```

### Error Handling

```python
def execute(self, arguments):
    try:
        # Your business logic
        return {"success": True, "result": data}
    
    except frappe.DoesNotExistError:
        return {"success": False, "error": "Document not found"}
    
    except frappe.PermissionError:
        return {"success": False, "error": "Permission denied"}
    
    except Exception as e:
        # Log but don't expose internals
        frappe.log_error(f"Tool error: {str(e)}", "My Tool Error")
        return {"success": False, "error": "An error occurred"}
```

### Input Schema

```python
# Good - specific, with examples
self.inputSchema = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "string",
            "description": "Customer ID (e.g., CUST-00001)"
        },
        "include_history": {
            "type": "boolean",
            "description": "Include transaction history",
            "default": False
        },
        "limit": {
            "type": "integer",
            "description": "Max records to return",
            "default": 10,
            "minimum": 1,
            "maximum": 100
        }
    },
    "required": ["customer_id"]
}
```

---

## Part 6: Complete Example - Tender Automation Plugin

### File Structure

```
tender_automation/
├── assistant_core/
│   ├── __init__.py
│   └── plugins/
│       ├── __init__.py
│       └── tender_tools/
│           ├── __init__.py
│           ├── plugin.py
│           └── tools/
│               ├── __init__.py
│               ├── search_tenders.py
│               ├── get_tender_details.py
│               └── create_tender.py
└── hooks.py
```

### plugin.py

```python
# tender_automation/assistant_core/plugins/tender_tools/plugin.py

from typing import Any, Dict, List, Tuple, Optional

import frappe
from frappe import _
from frappe_assistant_core.plugins.base_plugin import BasePlugin


class TenderToolsPlugin(BasePlugin):
    """Plugin for tender management operations"""

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "tender_tools",
            "display_name": "Tender Tools",
            "description": "Tools for managing tenders, bids, and procurement",
            "version": "1.0.0",
            "author": "Your Company",
            "category": "Procurement",
            "dependencies": [],
            "requires_restart": False,
        }

    def get_tools(self) -> List[str]:
        return [
            "search_tenders",
            "get_tender_details",
            "create_tender",
        ]

    def validate_environment(self) -> Tuple[bool, Optional[str]]:
        # Check if Tender DocType exists
        if not frappe.db.table_exists("Tender"):
            return False, "Tender DocType not found"
        
        # Check permissions
        can_enable, error = self._check_permissions(["Tender"])
        return can_enable, error
```

### tools/search_tenders.py

```python
# tender_automation/assistant_core/plugins/tender_tools/tools/search_tenders.py

from typing import Any, Dict
import frappe
from frappe_assistant_core.core.base_tool import BaseTool


class SearchTenders(BaseTool):
    """Search tenders by various criteria"""

    def __init__(self):
        super().__init__()
        self.name = "search_tenders"
        self.description = """
Search tenders by status, date range, or keyword.
Use when user wants to find tenders or list all tenders.
Returns tender names, titles, status, and deadlines.
        """.strip()
        self.category = "Tender Tools"
        self.source_app = "tender_automation"

        self.inputSchema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status",
                    "enum": ["Open", "Closed", "Draft", "Cancelled"]
                },
                "keyword": {
                    "type": "string",
                    "description": "Search in title and description"
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)"
                },
                "to_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100
                }
            }
        }

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filters = {}

        if arguments.get("status"):
            filters["status"] = arguments["status"]

        if arguments.get("from_date"):
            filters["submission_date"] = [">=", arguments["from_date"]]

        if arguments.get("to_date"):
            filters["submission_date"] = ["<=", arguments["to_date"]]

        try:
            tenders = frappe.get_all(
                "Tender",
                filters=filters,
                fields=["name", "title", "status", "submission_date", "estimated_value"],
                limit=arguments.get("limit", 20)
            )

            # Apply keyword filter if provided
            if arguments.get("keyword"):
                keyword = arguments["keyword"].lower()
                tenders = [
                    t for t in tenders
                    if keyword in (t.get("title") or "").lower()
                ]

            return {
                "success": True,
                "count": len(tenders),
                "tenders": tenders
            }

        except Exception as e:
            return {"success": False, "error": str(e)}


search_tenders = SearchTenders
```

### hooks.py

```python
# tender_automation/hooks.py

def get_app_utils():
    return {
        "assistant_plugins": [
            "tender_automation.assistant_core.plugins.tender_tools.plugin.TenderToolsPlugin"
        ]
    }
```

---

## Summary

| Approach | Best For | Steps |
|----------|----------|-------|
| **Simple Tool** | Single tool, quick addition | 1. Create class → 2. Register in hooks |
| **Plugin** | Multiple related tools | 1. Create plugin.py → 2. Create tools → 3. Register |

### Key Files to Remember

- **Tool**: Inherits from `BaseTool`, implements `execute()`
- **Plugin**: Inherits from `BasePlugin`, implements `get_info()`, `get_tools()`, `validate_environment()`
- **Registration**: `get_app_utils()` in `hooks.py`

---

## Related Documentation

- [MCP Integration Guide](../MCP_INTEGRATION_SETUP_GUIDE.md)
- [007-mcp-integration-and-cleanup.md](./007-mcp-integration-and-cleanup.md)
- Assistant Core source: `frappe_assistant_core/plugins/`
