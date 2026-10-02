# Connect an MCP server to an agent

Step-by-step. For the concepts (and what MCP calls can and cannot do), read [007](specifications/007-mcp-integration-and-cleanup.md) first.

MCP (Model Context Protocol) is a standard way for a program to offer tools to an AI agent. In `frappe_ai` an **AI MCP Connection** describes one MCP server, and an agent uses it through its **MCP Connections** table.

> Only a System Manager can create or edit connections. A `stdio` connection starts a program on your server.

## Option 1: Frappe Assistant Core over HTTP

Use this to give an agent Assistant Core's tools through its MCP endpoint (a `streamable-http` server). If the agent can use FAC tools **directly**, prefer binding them as *Plugin Tools* instead ([008](specifications/008-how-to-create-tools.md)); direct tools get Frappe's approval and budget checks, MCP calls do not.

1. **Create credentials.** Open the Frappe **User** that the connection will act as, go to *API Access* and generate an API key and secret. Save both.
2. **Enable the user for Assistant Core.** On the same user, tick Assistant Core's *Enable Assistant* checkbox (the field name comes from that app) and save.
3. **Create the connection.** New **AI MCP Connection**:
   - *Connection Name:* `Assistant Core`
   - *Connection Type:* `streamable-http`
   - *Endpoint URL:* `https://<your-site>/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp`
   - *API Key* and *API Secret:* from step 1.
4. **Test.** Open the connection and click **Test Connection**. You should see `Connected (N tools)`. The discovered tools are listed in the *Tools* table.
5. **Attach it.** Open the **AI Agent**, add a row under **MCP Connections**, pick the connection, and optionally put a JSON list of tool names in *Include Tools* to limit what the agent sees.
6. **Try the agent** in a chat.

**Identity warning:** the connection uses *one* user's credential for every run. The server therefore sees all chats as that user. Use a restricted account, and read-only tools, unless you have a delegated per-user credential.

## Option 2: a local program (stdio)

Use for development or small tools that run next to Frappe.

| Field | Example | Rule |
|---|---|---|
| Connection Type | `stdio` | |
| Command | `python` | The executable only: one word, no spaces, no `&&`, `;`, `|`, quotes or `$` |
| Command Arguments | `["-m", "my_pkg.mcp_server"]` | JSON list of strings |
| Environment Variables | `{"API_URL": "http://localhost"}` | JSON object; names like `MY_VAR`; **values must be strings** (write `"8080"`, not `8080`) |

If you paste an old one-line command such as `python -m my_pkg.mcp_server` into *Command*, it is split into the executable and arguments when you save.

You can also paste a standard MCP config instead of filling fields. Use *MCP Config* with a single server:

```json
{ "mcpServers": { "My Server": { "command": "python", "args": ["-m", "my_pkg.mcp_server"], "env": { "K": "v" } } } }
```

On save the values are copied into the normal fields and *MCP Config* is cleared. (From code: `create_mcp_connection_from_json`.)

## Option 3: a remote server over SSE

| Field | Example |
|---|---|
| Connection Type | `SSE` |
| Endpoint URL | `https://mcp.example.com/sse` (must start with `http://` or `https://`) |

## After connecting

- **Health.** The connection is checked every 5 minutes; *Is Connected* and *Status Message* update. A connection marked not connected is skipped when a run starts.
- **Limiting tools.** Use the agent row's *Include Tools* list. A tool that the agent already has directly (as a Plugin Tool) is hidden from the MCP copy.

## Troubleshooting

| Symptom | Check |
|---|---|
| `Connection Failed` / not connected | Is the URL reachable *from the server*? Is the key/secret right and not expired? Is the user enabled in Assistant Core? |
| Saving is rejected | Read the message. Common: Command contains spaces or shell characters; Command Arguments is not a list of strings; an environment value is not a string; the endpoint URL is not `http(s)` |
| No tools listed | Click *Test Connection* again; check *Include Tools*; check the credential's permissions on the server |
| Auth errors on `streamable-http` | The service sends `Authorization: token <api_key>:<api_secret>`; confirm both fields are filled |
| Works in the form, fails in chat | The service must be able to run the command or reach the URL too; both run from the service process's environment |

## Field summary

| Field | Needed for | Notes |
|---|---|---|
| Connection Name | all | unique |
| Connection Type | all | `stdio`, `SSE`, `streamable-http` |
| Endpoint URL | SSE, streamable-http | `http(s)` |
| Command, Command Arguments | stdio | see Option 2 |
| Environment Variables | optional | strings only |
| API Key, API Secret | streamable-http | Password fields |
| Include Tools (on the agent's row) | optional | JSON list of tool names |
