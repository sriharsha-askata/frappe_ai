# Lesson 3 - Building an agent (`service/builder.py`)

Supporting file: `lib/model.py` (`create_openai_compatible_model`).

## Why it exists
`get_run_config` returns plain data (JSON). Agno needs live Python objects: a model client, tool functions, an `Agent`. `AgentBuilder.build(run, user, approved_call_ids)` converts one into the other, once per request. Nothing is cached: the service is stateless.

## Concepts
- **Agent loop** (Agno docs): build context (system message, history, user message) -> call model -> if the model requests tool calls, execute them and append results -> repeat until the model answers without a tool call.
- **Tool schema**: the model never sees your code, only a name, a description and a JSON-schema of parameters. It replies with a tool call (name + JSON arguments); the harness runs it.
- **OpenAI-compatible API**: a de-facto request format that many providers (OpenAI, Ollama, Gemini, ...) accept. One client class plus a different `base_url` and key covers them all (ADR 0014).
- **Sampling params**: `temperature` (randomness) and `top_p` (nucleus cut-off). Reasoning models may reject them.

## Walkthrough
1. `get_run_config` (Lesson 2); a `FrappeClientError` becomes `AgentBuildError`.
2. `_build_model` -> `_apply_agent_sampling` merges the agent's `temperature`/`top_p` over model defaults, except for `reasoning` agents -> `create_openai_compatible_model` creates Agno's `OpenAIChat` with `base_url`, `api_key`, `timeout`, bounded `max_retries` (SDK-level) and `retries` (Agno-level, default 0, so a whole orchestration turn is not replayed). Config errors are normalised via `normalize_provider_error` into `AgentBuildError(message, code)`.
3. `_build_tool` per tool in `config["tools"]`; `_build_mcp_tools` for remote MCP servers (stdio / streamable-http / SSE; disconnected or failing ones are skipped with a warning).
4. `Agent(...)` is created with: `instructions=None` and `system_message_role="system"` (instructions already arrive as a system message in `messages`; avoids the `developer` role some backends reject); `db=None`, `add_history_to_context=False` (Frappe is the only conversation store); `telemetry=False`; `tool_call_limit=max_iterations` (otherwise the field would do nothing).

## The key function: `_build_tool`
It returns an Agno `Function` whose entrypoint never runs business logic locally:
```
async def entrypoint(*, fc=None, **kwargs):
    call_id = fc.call_id                      # Agno injects fc by NAME
    if requires_confirmation and not auto_approve and call_id not in approved_call_ids:
        raise PendingConfirmation(call_id, name, kwargs)      # pause
    response = await frappe_client.dispatch_plugin_tool(name, user, kwargs, run, call_id)
    return {"error": ...} or response["result"]
```
- Only `source == "fac"` tools are supported; anything else raises `AgentBuildError`.
- `PendingConfirmation` is a plain `Exception` on purpose: Agno records a plain exception per tool call and carries on, so several confirmation questions can be collected in one turn. An Agno `AgentRunException` would abort the whole run. Agno keeps only the string form, so `__str__` encodes `MARKER:call_id:name:json_args` and the chat route (Lesson 4/6) parses it back.
- `approved_call_ids` only lifts the gate; the approval is also recorded and enforced in Frappe (dispatch), so the service cannot self-approve.

## Example (hypothetical)
Agent has tool `create_task` (requires_confirmation). Model replies with a tool call `create_task({"subject": "Call Ana"})`. Entrypoint: not approved -> raises `PendingConfirmation`; run pauses with a question. User approves; resume builds the agent with `approved_call_ids={"call_1"}`; the approved call is dispatched to Frappe (Lesson 6).

## Production notes
- Handled: no secrets or DB in the service, retries bounded, single transport, iteration cap, confirmation gate backed by Frappe-side enforcement.
- Partial: MCP tools are built from connection config and bypass approvals/budgets (known gap); MCP failures are only logged.
- Missing: no model-output validation beyond Agno; no prompt-injection defence at this layer (trigger runs add a guard in `triggers/`).

## Checkpoint questions
1. Why `instructions=None` and `add_history_to_context=False`? Where do instructions and history actually come from?
2. Why is `PendingConfirmation` a plain exception, and what would change if it subclassed `AgentRunException`?
3. If the service passed a forged `approved_call_ids`, why could the tool still not run?
4. What does `tool_call_limit` bound, and what happens to a tool call beyond the limit?
