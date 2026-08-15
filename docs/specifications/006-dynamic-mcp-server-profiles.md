# 006 — Dynamic MCP Server Profiles

**Status:** Planned architecture.

**Purpose:** Define how `frappe_ai` publishes centrally-defined tools through
configuration-driven MCP servers without requiring one `mcp_server.py` module per app
or per tool group.

## 1. Overview

`AI Tool` remains the central catalog for tool definitions. An MCP server profile selects
a subset of those tools, and one reusable MCP runner registers the selected tools at
runtime.

```text
AI Tool catalog
    │
    ▼
MCP Server Profile
    │
    ▼
Generic MCP Runner
    │
    ▼
stdio / SSE / Streamable HTTP MCP server
```

The implementation functions still live in application code or in the existing Script
tool sandbox. What disappears is the repeated registration boilerplate:

```python
mcp = FastMCP("Tender MCP")

@mcp.tool()
def extract_tender_documents(...):
    ...
```

Instead, the generic runner loads a profile and registers its selected tools dynamically.

## 2. Tool Definitions

### 2.1 Existing `AI Tool` catalog

Tools are defined in the existing `AI Tool` DocType:

| Field | Purpose |
|---|---|
| `slug` | Canonical tool name exposed to agents and MCP clients |
| `title` | Human-readable tool title |
| `type` | `Imported` or `Script` |
| `import_path` | Dotted Python path for an Imported tool |
| `code` | Safe-exec source for a Script tool |
| `description` | LLM- and MCP-facing description |
| `enabled` | Whether the tool may be resolved |
| `requires_confirmation` | Confirmation policy for native agent execution |

Imported tools still require implementation code in the codebase. For example:

```python
# tender_automation/tender_automation/ai/tools.py

def extract_tender_documents(documents, template_parameters):
    ...
```

The corresponding `AI Tool` row stores:

```text
slug: extract_tender_documents
type: Imported
import_path: tender_automation.tender_automation.ai.tools.extract_tender_documents
```

The path is necessary to locate the implementation. A separate MCP server module is not
necessary.

### 2.2 Tool references

Server profiles may refer to a tool by:

1. Exact `AI Tool.slug` or document name.
2. Exact `AI Tool.import_path`.
3. An explicitly allowlisted dotted Python path.

Resolution must fail clearly when a reference is missing, disabled, invalid, or
ambiguous. Direct Python paths are executable configuration and are restricted to
privileged users and approved module prefixes.

The approved-prefix allowlist is a **hardcoded application constant**
(`frappe_ai/mcp/config.py`), not a DocType field or other Frappe-editable
configuration. This mirrors [ADR 0006](../decisions/0006-unified-safe-exec-namespace.md)'s
reasoning: DocType-editable configuration is a weaker trust tier than versioned
code, and a privilege-escalation control (which module prefixes may be invoked
as a tool) must not live in that weaker tier. Only a code change — reviewed and
deployed through the normal app-release process — may add a prefix.

"Privileged users" means **System Manager**, and the requirement applies at
both levels: creating or editing an `AI MCP Server Profile` requires System
Manager, and adding or editing an `AI MCP Server Tool` child row with
`reference_type = Import Path` requires System Manager, even when the row is
saved as part of a profile a non-System-Manager could otherwise view. A
child-table permission or `validate()` check must enforce the row-level
restriction independently of the parent doctype's permissions, since child
tables do not always inherit the parent's write restrictions per field.

MCP discovery uses the remote MCP `tool_name`. MCP does not normally provide a Python
import path for a tool. Any vendor-specific path or metadata is retained as raw metadata,
not treated as a local implementation path.

## 3. MCP Server Profiles

`AI MCP Server Profile` represents one server that `frappe_ai` publishes.

The profile owns:

- server name and description;
- transport configuration;
- standard MCP configuration JSON;
- selected tools;
- startup and health status;
- tool resolution errors.

The profile is distinct from `AI MCP Connection`:

| Model | Role |
|---|---|
| `AI MCP Connection` | Client connection to an external MCP server |
| `AI MCP Server Profile` | Local configuration for publishing selected tools as an MCP server |
| `AI MCP Tool` | Discovery record for a tool observed on an external MCP server |
| `AI MCP Server Tool` | Publication/selection row for a tool exposed by a local profile |

The separate models prevent externally discovered tools from being confused with tools
that this application publishes.

## 4. Profile Tool Selection

`AI MCP Server Tool` is a child table of `AI MCP Server Profile`.

It contains:

| Field | Purpose |
|---|---|
| `tool_reference` | AI Tool name, slug, or approved import path |
| `reference_type` | `AI Tool` or `Import Path` |
| `ai_tool` | Resolved link to an existing `AI Tool`, when applicable |
| `import_path` | Resolved path for direct-path references |
| `exposed_name` | Optional MCP-specific name override |
| `description_override` | Optional profile-specific description |
| `enabled` | Include/exclude this tool from the profile |
| `schema_snapshot` | Schema last resolved for publication |
| `resolution_status` | Resolved or failed |
| `resolution_error` | Human-readable failure detail |

Disabling a row removes the tool from that profile only. It does not disable the global
`AI Tool` or remove the tool from other profiles.

## 5. Dynamic Configuration

Profiles accept standard MCP-style configuration JSON. A stdio profile may look like:

```json
{
  "server_name": "Tender MCP",
  "transport": "stdio",
  "command": "/home/a/harsha/harsha/env/bin/python",
  "args": [
    "-m",
    "frappe_ai.mcp.runner",
    "--profile",
    "Tender MCP"
  ],
  "env": {
    "FRAPPE_SITE": "tact.local"
  },
  "tools": [
    "extract_tender_documents",
    "match_historical_enquiries"
  ]
}
```

A second profile can use the same runner and expose a different subset:

```json
{
  "server_name": "Sales MCP",
  "transport": "stdio",
  "command": "/home/a/harsha/harsha/env/bin/python",
  "args": [
    "-m",
    "frappe_ai.mcp.runner",
    "--profile",
    "Sales MCP"
  ],
  "tools": [
    "search_sap_sales_data",
    "get_customer_details"
  ]
}
```

Standard multi-server configuration can be imported by expanding each named server into
an independent profile:

```json
{
  "mcpServers": {
    "Tender MCP": {
      "command": "python",
      "args": ["-m", "frappe_ai.mcp.runner"],
      "tools": ["extract_tender_documents"]
    },
    "Sales MCP": {
      "command": "python",
      "args": ["-m", "frappe_ai.mcp.runner"],
      "tools": ["search_sap_sales_data"]
    }
  }
}
```

Unknown vendor-specific keys should be preserved, while unsupported transports or
missing required fields must fail validation.

## 6. Generic Runner

The reusable implementation is planned under:

```text
frappe_ai/mcp/
├── __init__.py
├── config.py
├── resolver.py
├── registry.py
├── server.py
└── runner.py
```

### `config.py`

Loads and normalizes profile configuration from a Frappe profile or a JSON file. It
supports `--profile` and `--config` entry points and never logs secrets.

### `resolver.py`

Resolves AI Tool names, import paths, and approved direct Python paths into a normalized
tool definition containing:

- exposed name;
- description;
- input schema;
- callable;
- source type and source record.

Imported tools are resolved through the existing resolver. Script tools continue to use
the hardened `safe_exec` path.

### `registry.py`

Builds the profile registry, applies name and description overrides, rejects duplicate
exposed names, and stores the final schemas used by `tools/list`.

### `server.py`

Provides one dynamic MCP server implementation. It exposes the registry through
`tools/list` and dispatches `tools/call` to the resolved callable. It returns structured
errors for missing tools, invalid arguments, permission failures, and execution failures.

The server must publish explicit schemas from the registry so Script tools and dynamically
resolved functions do not degrade to a generic `**kwargs` schema.

### `runner.py`

Provides the common process entry point:

```bash
python -m frappe_ai.mcp.runner --profile "Tender MCP"
```

or:

```bash
python -m frappe_ai.mcp.runner --config /path/to/mcp_config.json
```

The runner initializes the selected Frappe site when required, resolves every selected
tool before startup, and refuses to start with an invalid profile.

## 7. Transport and Security Boundaries

Supported transports are:

- stdio;
- SSE;
- Streamable HTTP.

Direct import paths are executable configuration. Only System Managers may modify server
profiles, and configured paths must match approved module prefixes.

Script tools retain the existing hardened `safe_exec` namespace. The MCP layer must not
provide a broader execution environment.

Database-backed Frappe tools require a Frappe site and authenticated execution context.
The runner must preserve Frappe permission checks and acting-user behavior. It must not
fall back to unrestricted Administrator execution for normal tool calls.

`frappe_assistant_core` remains the preferred owner of authenticated HTTP-hosted Frappe
MCP endpoints. `frappe_ai` should not create a competing Frappe-hosted MCP server. The
generic runner is appropriate for standalone or app-specific stdio profiles and for
adapters that intentionally delegate to Assistant Core.

## 8. External MCP Discovery

External server discovery is separate from local publication.

`AI MCP Connection` will:

1. parse dynamic standard MCP configuration;
2. connect using stdio, SSE, or Streamable HTTP;
3. perform the MCP handshake;
4. call `tools/list`;
5. upsert `AI MCP Tool` discovery rows;
6. mark missing tools unavailable;
7. optionally match remote names to native `AI Tool` rows.

The discovery child table is an observation/catalog of a remote server. It does not create
local implementations and does not make the remote tool a native `AI Tool`.

## 9. Migration from App-Specific Servers

For an existing server module:

1. Move or retain implementation functions in a normal application tool module.
2. Create corresponding `AI Tool` rows with slug, description, and import path.
3. Create an `AI MCP Server Profile` selecting those tools.
4. Replace the old command with the generic runner command.
5. Verify the new `tools/list` response matches the old server.
6. Remove the app-specific `mcp_server.py` only after successful verification.

For Tender, the target is:

```text
tender_automation/.../ai/tools.py
AI Tool rows
Tender MCP Server Profile
frappe_ai.mcp.runner
```

The custom tool implementations remain version-controlled. Only repetitive MCP server
registration code is removed.

## 10. Planned API and UI

The profile UI should provide:

- tool search by slug, title, and import path;
- child-table selection and deselection;
- tool resolution and schema preview;
- per-tool resolution errors;
- profile command/config preview;
- server status and exposed tool count.

Planned APIs include profile validation, tool resolution, exposed-tool preview, profile
startup/testing, status checks, schema refresh, and standard MCP JSON import.

Responses must distinguish native AI Tools, locally published MCP tools, and externally
discovered MCP tools. Secrets must not appear in frontend responses, logs, or run
snapshots.

## 11. Acceptance Criteria

- Two MCP profiles can use the same generic runner with different tool selections.
- Adding or removing a profile tool requires no new `mcp_server.py` file.
- Tools remain defined and tested in application code or the existing AI Tool sandbox.
- Imported tools resolve from their codebase paths.
- Script tools retain their generated schemas and sandbox behavior.
- `tools/list` exposes exactly the enabled tools in a profile.
- `tools/call` invokes the correct resolved implementation.
- Invalid, disabled, missing, or ambiguous references prevent startup with clear errors.
- Existing external MCP connections continue to discover and catalog remote tools.
- Assistant Core remains the preferred authenticated HTTP MCP server for Frappe-native
  integrations.
