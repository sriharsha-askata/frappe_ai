# 006 — Dynamic MCP server profiles (not built)

**Status: an old proposal that was never implemented. Nothing in the code follows it.**

The idea was a table of "MCP server profiles" that would select a subset of `AI Tool` records and publish them through one generic MCP server, so apps would not each need a hand-written `mcp_server.py`. It was dropped, and its two DocTypes (`ai_mcp_server_profile`, `ai_mcp_server_tool`) no longer exist.

What the app does instead:

- **Remote or local MCP servers** are described by `AI MCP Connection` records and attached to an agent with `AI Agent MCP Connection` rows. See [007](007-mcp-integration-and-cleanup.md) and the [MCP setup guide](../MCP_INTEGRATION_SETUP_GUIDE.md).
- **Tools from Frappe Assistant Core (FAC)** are bound directly to an agent with `AI Agent Plugin Tool` rows, with no MCP in between. See [008](008-how-to-create-tools.md).

Read this note only to know why you will not find profile code. If you want to revisit the idea, start from [007](007-mcp-integration-and-cleanup.md).
