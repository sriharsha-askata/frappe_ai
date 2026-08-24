# Frappe Assistant Core Integration Guide

> The dynamic MCP publication model in [006 — Dynamic MCP Server Profiles](specifications/006-dynamic-mcp-server-profiles.md)
> is archived. The active integration and migration plan is [007 — MCP Integration & Cleanup](specifications/007-mcp-integration-and-cleanup.md).

This document explains how `frappe_ai` can use `frappe_assistant_core`, what
that gives us, and which parts of `frappe_ai` should eventually be reduced. It is a
boundary and migration guide; the current status is tracked in the Assistant Core
progress document.

## Current implementation (2026-08-21)

`frappe_ai` now supports two Assistant Core paths:

- **Direct plugin linkage** through `AI Agent Plugin Tool`, preferred for tools hosted
  on the same site. These calls resolve through Assistant Core's in-process registry and
  return through `frappe_ai`'s acting-user dispatch boundary.
- **Remote MCP connections** through `AI MCP Connection`, used for external servers and
  retained as a migration fallback for tender tools.

The legacy `AI Tool`/`AI Agent Tool` records remain for compatibility and migration input;
they are not being deleted as part of the current work.

## Executive Recommendation

Use `frappe_assistant_core` as the canonical provider for generic Frappe business tools.
Use direct plugin linkage for same-site tools and its MCP endpoint for remote or deliberate
transport-based connections.

Keep `frappe_ai` focused on:

- agent, model, session, and run configuration;
- asynchronous Agno orchestration and streaming;
- knowledge bases, memory, and triggers;
- confirmation and run lifecycle handling;
- tools that are specific to `frappe_ai` and do not already exist in Assistant
  Core.

Do not create a second implementation of Assistant Core's document, metadata,
search, report, workflow, or generic code-execution tools inside `frappe_ai`.

The target architecture is:

```text
Browser
  -> frappe_ai / Frappe creates AI Run and run token
  -> frappe_ai FastAPI builds Agno Agent
  -> same-site: direct Assistant Core registry dispatch
  -> remote/fallback: Agno MCP client calls Assistant Core MCP endpoint
  -> Assistant Core authenticates, validates permissions, and executes BaseTool
  -> Assistant Core records its tool audit
  -> frappe_ai records the agent run and transcript
```

## How This Helps

### One canonical Frappe tool catalogue

Assistant Core already discovers plugin tools and exposes their schemas through
MCP. `frappe_ai` does not need to copy those tools into `AI Tool` rows or keep a
second Python registry synchronized with Assistant Core.

### One permission boundary

Assistant Core filters tools by enabled state, role access, and Frappe
permissions before returning `tools/list`. It repeats the checks during
`tools/call` and executes through `BaseTool._safe_execute()`.

This prevents the agent service from becoming a privileged Frappe service
account and preserves the existing Assistant Core security model.

### Less duplicated execution code

The following concerns already exist in Assistant Core and should not be
reimplemented in `frappe_ai` for Assistant Core-owned tools:

- plugin discovery;
- JSON Schema generation and argument validation;
- enabled/disabled tool configuration;
- role-based tool access;
- DocType permission checks;
- dependency checks;
- execution timing and error normalization;
- tool audit logging;
- MCP `tools/list` and `tools/call` handling.

### Better external-client support

Assistant Core already exposes StreamableHTTP, OAuth discovery, OAuth bearer
authentication, and API key/secret authentication. The same tool catalogue can
therefore be used by `frappe_ai`, Claude Desktop, MCP Inspector, and other MCP
clients without adding a separate server process.

## Current Ownership Map

| Capability | Current owner | Future canonical owner | Action in `frappe_ai` |
|---|---|---|---|
| Generic document read/list/create/update/delete | Both apps | Assistant Core | Direct FAC linkage preferred; MCP fallback during migration |
| DocType metadata and search | Both apps | Assistant Core | Migrate after schema/permission parity verification |
| Reports and workflows | Assistant Core or plugins | Assistant Core | Consume through MCP |
| Generic Python/code execution | Both apps | Explicit per-tool decision | Do not merge automatically |
| Agent/session/run lifecycle | `frappe_ai` | `frappe_ai` | Keep |
| Agno model calls and streaming | `frappe_ai` | `frappe_ai` | Keep |
| Knowledge and memory | `frappe_ai` | `frappe_ai` | Keep |
| Triggers and scheduled agent runs | `frappe_ai` | `frappe_ai` | Keep |
| Assistant Core plugin tools | Assistant Core | Assistant Core | Consume through direct linkage when same-site; MCP when remote |
| Agent-specific custom tools | `frappe_ai` `AI Tool` | `frappe_ai` initially | Keep when no FAC equivalent exists |
| MCP endpoint and tool registry | Assistant Core | Assistant Core | Do not duplicate |
| Agent connection selection | `frappe_ai` | `frappe_ai` | Keep as agent configuration |

The important distinction is ownership. `frappe_ai` may decide which MCP server
and which tools an agent uses, but Assistant Core remains authoritative for the
tools it owns and for permission enforcement.

## What Should Be Reduced in `frappe_ai`

### Duplicate built-in Frappe tools

`frappe_ai/tools/builtins.py` currently seeds generic tools such as `describe`,
`read`, and `execute` into the `AI Tool` DocType. Assistant Core has a broader
plugin-based catalogue containing equivalent document and metadata tools.

The migration should:

1. Compare each `frappe_ai` builtin with the Assistant Core equivalent.
2. Verify schema, result shape, permission behavior, and confirmation needs.
3. Configure the Assistant Core MCP connection on the relevant agents.
4. Update the agent builder to use the Assistant Core tools.
5. Stop creating duplicate builtin rows once all consumers are migrated.
6. Remove only the rows and implementations proven to be redundant.

Do not delete the builtins first. Existing agents, tests, and stored agent
configurations may still refer to their `AI Tool` names.

### Duplicate tool registry behavior

`frappe_ai` should not introduce a registry that imports Assistant Core plugin
classes. The existing `AI Tool` resolver remains responsible only for native
`frappe_ai` tools during the transition.

### Duplicate MCP server logic

`frappe_ai` should remain an MCP client for Assistant Core. It should not add a
second Frappe-hosted MCP server, a second `tools/list` implementation, or a
Python file that dynamically imports Assistant Core tools from JSON.

### Duplicate tool health logic

The current `AI MCP Connection` health check should verify the configured MCP
endpoint and discovered tools. It should not reproduce Assistant Core's tool
registry checks. Assistant Core remains responsible for tool availability and
user-specific access.

## What Should Remain in `frappe_ai`

The following are not redundant with Assistant Core and should remain:

- `AI Agent`, `AI Model`, `AI Session`, and `AI Run`;
- Agno `AgentBuilder` and the FastAPI run loop;
- Frappe-to-FastAPI run configuration and persistence callbacks;
- confirmation pause/resume behavior tied to `AI Run`;
- `AI Knowledge Base`, source ingestion, retrieval, and memory;
- `AI Trigger` and scheduled dispatch;
- native `AI Tool` rows that implement agent-specific behavior;
- agent-level MCP connection selection and `include_tools` policy;
- transcript and run-level observability.

An Assistant Core tool call should appear in the `AI Run` transcript as an MCP
tool call, while Assistant Core retains its own detailed tool audit record.

## Integration Data Flow

### Agent configuration

`AI Agent` continues to select MCP connections through
`AI Agent MCP Connection`:

```json
{
  "mcp_connection": "Frappe Assistant Core",
  "include_tools": [
    "get_document",
    "list_documents",
    "search_documents"
  ]
}
```

`include_tools` is an agent-level narrowing rule. It is not a permission grant.
Assistant Core must still filter tools for the authenticated user and re-check
access during execution.

### Run configuration

`frappe_ai` resolves both `AI Agent.plugin_tools` and `AI Agent.mcp_connections` in
`frappe_ai/api/service.py`. Direct plugin bindings use registry metadata; MCP bindings
send connection details to FastAPI for the duration of a run. The current MCP
configuration distinguishes run snapshots from transient connection credentials:

```text
Safe to send to FastAPI:
- endpoint URL
- transport
- connection name
- include_tools
- timeout policy

Do not place in `AI Run.config_snapshot`, logs, or persistent service state:
- API secrets
- OAuth refresh tokens
- long-lived credentials
```

The current streamable-http path passes the configured API key/secret to the service
in the per-run request so Agno can open the MCP client; the values are not included in
the run snapshot. Credential minimization/rotation remains a production-hardening item.

### Runtime tool construction

`AgentBuilder.build()` appends direct plugin tools and MCP tools to the Agno agent. The
MCP connection builder supports Assistant Core's StreamableHTTP transport in addition to
stdio/SSE connections.

The runtime should use the installed Agno API for StreamableHTTP and pass:

- the Assistant Core endpoint;
- authentication headers or an approved credential provider;
- the agent's `include_tools` allowlist;
- discovery and call timeouts.

The installed Agno constructor was verified on 2026-08-19: StreamableHTTP uses
`MCPTools(transport="streamable-http", server_params=StreamableHTTPClientParams(...))`.
Treating SSE and StreamableHTTP as interchangeable remains incorrect.

## Identity and Security Requirement

This is the critical integration issue.

`frappe_ai` knows the acting user because the run token and Frappe service calls
carry that identity. Assistant Core's MCP endpoint authenticates OAuth bearer
tokens or API key/secret credentials. A generic shared API key would make all
agent runs appear as one Assistant Core user and could defeat per-user filtering.

Before enabling mutating Assistant Core tools, choose and implement one of these
identity mechanisms:

### Preferred: delegated user credential

Authenticate the MCP request as the actual Frappe user. Assistant Core can then
apply the same user's roles and DocType permissions.

### Controlled bridge: signed acting-user request

If the service cannot obtain a user credential, add a narrowly scoped bridge in
Assistant Core that accepts a request signed by the trusted `frappe_ai` service,
contains the run and acting user, verifies run ownership with Frappe, calls
`frappe.set_user(acting_user)`, and then enters the Assistant Core registry.

This bridge must not accept an arbitrary user field from an unauthenticated
client. It must validate the run, user, expiry, and service signature for every
request.

### Not acceptable for general use: shared privileged API key

A shared key is acceptable only for an explicitly restricted, read-only
deployment. It must not be the default for agents that can mutate Frappe data.

## Current implementation and remaining work

### `frappe_ai` connection model

`AI MCP Connection` now supports:

- `streamable-http` transport;
- endpoint URL;
- authentication mode;
- per-connection credentials used transiently by the service;
- separate discovery and tool-call timeouts;
- connection health and last discovery result.

Preserve existing stdio/SSE records and behavior during migration.

### `frappe_ai` service configuration

The Frappe-to-FastAPI run configuration sends the selected MCP connection metadata and
transient authentication values needed to construct the client. Credentials are not
included in `AI Run.config_snapshot`, logs, or serialized agent configuration.
Credential rotation/minimization remains open hardening work.

### `AgentBuilder`

`_build_mcp_tools()` now:

- construct the correct StreamableHTTP client;
- pass authentication safely;
- pass `include_tools`;
- use per-connection timeouts;
- fail soft when Assistant Core is unavailable;
- log connection identity and error type without logging secrets.

### Connection health checks

`frappe_ai/api/mcp.py` performs a real MCP handshake and tool discovery. The current
health result records connection state, status text, and discovered tool metadata; more
granular operational classification remains optional hardening.

### Agent tool configuration UI

Keep the ability to select connections and allowlist tools, but label Assistant
Core tools as externally provided. The UI must not imply that `AI Tool` rows and
Assistant Core tools are the same storage type.

### Native tool migration

The compatibility report/migration utility compares native `AI Tool`
slugs with Assistant Core tool names. It should identify exact duplicates,
similar but incompatible schemas, `frappe_ai`-only tools, Assistant Core-only
tools, and agents dependent on each tool.

Only exact, tested replacements are migrated automatically. The current migration keeps
legacy DocTypes as compatibility records.

## Suggested Migration Order

The sequence below is the original migration shape. Phases 0–2 and the initial MCP
verification are complete. The active detailed plan, including direct FAC linkage,
tender migration, budget gaps, and legacy-DocType retention, is in [spec 007](specifications/007-mcp-integration-and-cleanup.md).

### Phase 0: Inventory

- Export all `AI Tool` rows and agent bindings.
- List Assistant Core tools from an authenticated `tools/list` response.
- Compare names, schemas, permissions, return shapes, confirmation behavior,
  and audit expectations.
- Identify overlapping `frappe_ai` builtins.

### Phase 1: Read-only remote integration

- Add StreamableHTTP connection support.
- Connect one test agent to Assistant Core.
- Use only read-only tools.
- Verify per-user `tools/list` results and acting-user propagation.
- Record both the AI Run and Assistant Core audit entries.

### Phase 2: Allowlisting and observability

- Enforce `include_tools` as a narrowing rule.
- Add MCP tool and connection names to run activity events.
- Add health and authentication diagnostics.
- Add timeout and unavailable-server behavior.

### Phase 3: Mutating tools

- Complete delegated identity or the signed acting-user bridge.
- Verify create/update/delete with non-privileged users.
- Add confirmation policy for mutating Assistant Core tools.
- Enable selected mutating tools by explicit agent allowlist.

### Phase 4: Reduce redundancy

- Migrate agents from duplicate `frappe_ai` builtins to Assistant Core.
- Stop seeding migrated duplicate builtin rows.
- Retire only duplicate runtime paths proven redundant; retain compatibility records and
  migration tooling.
- Keep a compatibility path until stored agents are migrated.

### Phase 5: Optional unification

Only after the remote integration is stable should we consider whether native
`AI Tool` rows should be represented in Assistant Core. This is optional and is
not required for the initial integration.

## Failure and Fallback Rules

- If Assistant Core is unavailable, continue with direct native/FAC tools and other
  healthy MCP connections where the agent configuration permits it.
- If authentication fails, report an MCP connection error; do not retry with a
  privileged fallback identity.
- If a requested tool is not returned by `tools/list`, treat it as unavailable.
- If Assistant Core rejects a call, return the MCP error to Agno and preserve
  the run activity record.
- Re-check permission at call time; discovery results are not authorization
  grants.
- Do not silently fall back from an Assistant Core tool to a duplicate native
  implementation with different permissions or semantics.

## Acceptance Criteria

The integration is ready for production consideration only when:

- an agent discovers selected Assistant Core tools through StreamableHTTP;
- a user sees only tools allowed by Assistant Core;
- adding a name to `include_tools` cannot grant access;
- a user without DocType permission cannot read or mutate that DocType through
  the agent;
- credentials are absent from run snapshots, logs, and FastAPI persistent state;
- Assistant Core and `frappe_ai` audits can be correlated by run/session/client
  identifiers;
- Assistant Core downtime does not crash unrelated agents;
- existing stdio/SSE connections continue to work;
- native `frappe_ai` tools remain functional during migration;
- duplicate builtins are removed only after dependent agents are migrated.

## Source References

Assistant Core:

- `apps/frappe_assistant_core/FRAPPE_ASSISTANT_CORE_CONTEXT.md`
- `apps/frappe_assistant_core/frappe_assistant_core/core/base_tool.py`
- `apps/frappe_assistant_core/frappe_assistant_core/core/tool_registry.py`
- `apps/frappe_assistant_core/frappe_assistant_core/mcp/server.py`
- `apps/frappe_assistant_core/frappe_assistant_core/mcp/tool_adapter.py`
- `apps/frappe_assistant_core/frappe_assistant_core/api/fac_endpoint.py`
- `apps/frappe_assistant_core/docs/internals/MCP_STREAMABLEHTTP_GUIDE.md`

`frappe_ai`:

- `apps/frappe_ai/frappe_ai/api/mcp.py`
- `apps/frappe_ai/frappe_ai/api/service.py`
- `apps/frappe_ai/frappe_ai/service/builder.py`
- `apps/frappe_ai/frappe_ai/tools/builtins.py`
- `apps/frappe_ai/frappe_ai/lib/resolver.py`
- `apps/frappe_ai/frappe_ai/api/dispatch.py`
- `apps/frappe_ai/docs/specifications/002-feature-mapping.md`
