# 008 — How to create a tool for an agent

A **tool** is an action the model can ask for: "read these records", "send this email", "look up a tender". The model only *requests* it. Frappe runs it, as the user, after checking permissions, approvals and limits ([001](001-architecture.md) §4).

This page shows the ways to add one. Pick the first that fits.

| Way | Use when | Effort |
|---|---|---|
| **A. Assistant Core tool class** (recommended) | You want a real tool in code, with tests | Low |
| **B. Script `AI Tool`** | You want an admin to write a small tool in the Desk, no deploy | Low, but the code runs in a sandbox with limited functions |
| **C. MCP server** | The tool already exists in another program | See [007](007-mcp-integration-and-cleanup.md) |

## A. Assistant Core tool class

Assistant Core (FAC, the `frappe_assistant_core` app) keeps a registry of tools. `frappe_ai` agents use tools from that registry. This app registers its own in `hooks.py`:

```python
assistant_tools = [
    "frappe_ai.assistant_tools.native.ExecuteTool",
    "frappe_ai.assistant_tools.native.SearchKnowledgeTool",
    ...
]
```

### Step 1: write the class

Subclass `BaseTool`, give it a name, a description the model will read, an input schema, and an `execute` method. A minimal example in the style of `frappe_ai/assistant_tools/native.py`:

```python
from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool


class CountOpenTasksTool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "count_open_tasks"
        self.description = "Count open Tasks, optionally for one project."
        self.inputSchema = {
            "type": "object",
            "properties": {"project": {"type": "string", "description": "Project name"}},
        }
        self.source_app = "your_app"
        self.category = "read_only"

    def execute(self, arguments: dict[str, Any]) -> Any:
        filters = {"status": "Open"}
        if arguments.get("project"):
            filters["project"] = arguments["project"]
        # frappe.get_list applies the current user's permissions
        return {"count": len(frappe.get_list("Task", filters=filters, limit=1000))}
```

### Step 2: register it

In your app's `hooks.py`:

```python
assistant_tools = ["your_app.assistant_tools.CountOpenTasksTool"]
```

Then run `bench --site <site> migrate` (or the Assistant Core registry refresh your FAC version provides) and `frappe_ai.api.fac_tools.sync_fac_tools` to copy the registry into `AI FAC Tool` records for the Desk.

### Step 3: give it to an agent

Open the agent and add the tool under **Plugin Tools**. Each binding has:

- **Enabled**
- **Requires confirmation** (default **on**). If on, the run pauses and the user must approve every call. Frappe reads this value from the binding when it decides whether to accept a call, so it cannot be skipped from the service. Turn it off only for read-only tools.

A tool is offered to the model only when Assistant Core reports it enabled and accessible to that user.

### What you get for free (and what you must do)

| Handled for you | Still your job |
|---|---|
| Running as the acting user | Use permission-aware calls inside `execute`: `frappe.get_list`, `doc.check_permission(...)`, never `ignore_permissions=True` or raw SQL on user data |
| The run must be live and owned by that user | Return JSON-safe values |
| Approval check and the per-run limits (`api/budgets.py`) | Raise a clear `frappe.throw` message on bad input |
| Errors returned to the model as `{"error": "..."}` (cut to 500 characters) | Keep results small; the model has to read them |

Tool calls count against the run budget. Calls named `create_document`, `update_document`, `delete_document`, `run_workflow` (and tools needing confirmation) count as changes. Large results are shortened to fit the model's context.

## B. Script `AI Tool` (admin-written)

Create an `AI Tool` with type **Script**. The code must:

- define a top-level function `main(...)` with typed arguments (the types become the JSON Schema; the code is read, not run, to build it);
- not use `*args` or `**kwargs`, and not call `main()` itself.

Script tools run in the `safe_exec` sandbox ([ADR 0006](../decisions/0006-unified-safe-exec-namespace.md)), where only permission-checked helpers such as `frappe.get_list`, `frappe.get_doc` and `frappe.db.get_value` exist; raw SQL and the query builder are removed. See the `execute` tool in `frappe_ai/tools/builtins.py` for the available names. Slug rules: lowercase, digits and underscores, starting with a letter. The `description` is what the model reads, so say when to use it. `AI Tool` is the older path: new tools should normally be class tools (A), bound as Plugin Tools.

## Writing tools that models use well

- **Name** by verb and object: `count_open_tasks`, not `tool1`. Lowercase with underscores.
- **Description:** one or two sentences on *what it does and when to use it*, and what it will not do. The model chooses tools from this text.
- **Input schema:** mark `required` fields; describe each property; use `enum` for fixed choices; keep arguments few.
- **Results:** small, structured, no secrets. Include an identifier so the model can refer back.
- **Errors:** explain what to fix ("Project 'X' not found"), not a stack trace.
- **Dangerous actions:** require confirmation, and describe in the prompt what will happen in plain words.

## Test it

- Unit-test `execute` directly with a user who lacks permission and check it refuses.
- Add your tool to a test agent and run a chat; check the `AI Run` shows the call in `tool_calls`.
- Dispatch tests in `frappe_ai/tests/test_api.py` show how to call `dispatch_plugin_tool` with a live run.

## Where this plugs into the code

`api/service.py::_resolve_agent_plugin_tools` (what the model is offered) → `service/builder.py::_build_tool` (the wrapper that pauses or calls Frappe) → `api/dispatch.py::dispatch_plugin_tool` (checks, then `get_tool_registry().execute_tool`).

*Not verified here:* the full plugin mechanism of Assistant Core (plugin classes with enable/disable and lifecycle hooks) lives in that app; see its own documentation.
