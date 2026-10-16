# Lesson 7 - Sessions, the context window and limits

Files: `doctype/ai_session/ai_session.py`, `doctype/ai_agent/ai_agent.json`, `doctype/ai_settings`, `lib/model.py` (`CHARS_PER_TOKEN`, `single_tool_result_char_budget`), `api/budgets.py` (Lesson 5).

## Why it exists
The model remembers nothing between calls. `AI Session` is the durable conversation; `build_prompt_messages()` turns it into the exact message list the service sends to the model on each run. This is where **context window management** happens.

## Concepts
- **Context window**: the maximum tokens (prompt + reply) a model can handle per call. Rough rule used here: `CHARS_PER_TOKEN = 4`.
- **Inline vs retrieval**: small attachments are pasted into the prompt; large ones are chunked, embedded and searched (RAG, Lesson 8).
- **Limits layered**: agent iteration cap (`max_iterations`, default 10), run budgets (`max_tool_calls` 50, `max_mutations` 20, `max_records_per_call` 100, `max_runtime_seconds` 600; all on `AI Agent`, snapshotted into the run), model timeouts (`AI Settings.request_timeout` 120 s, `stream_timeout` 600 s).

## Data model
`AI Session` has child tables `messages` (role, content, tool_call_id, tool_calls JSON, run) and `attachments` (file, extracted_text, mode Inline/Retrieval, run). `AI Run` rows hang off the session. The agent is fixed at creation (`_validate_agent_unchanged`).

## Flow
1. `start_run` -> `persist_turn`: first turn stores the agent's instructions as a `system` message; then the user message; each attachment is routed by `_route_attachment` using a threshold of `context_window * 4 chars * 0.5` (text beyond it is capped on the row). Retrieval attachments are chunked and embedded into LanceDB via `attachment_store` (failure demotes them to Inline permanently).
2. Service calls `get_run_config` -> `session.build_prompt_messages()` which, without changing stored rows:
   - appends the agent's memory block (`build_memory_block`) to the system message;
   - for each user message injects that run's inline attachments, limited by `_file_injection_budget` = window - 4096 reserved output tokens - existing dialogue chars;
   - for retrieval attachments adds a note and, for the latest turn only, the top 8 relevant chunks (embedding outage -> in-memory demotion to Inline, DB keeps Retrieval so it retries next time).
3. After the run, `AIRun.apply_result` appends only the new messages to the session (`append_run_messages`).

## Other behaviours
- `assert_not_blocked`: a new turn is refused while a run is Paused or truly Running; a Running run older than 300 s is auto-failed as abandoned.
- `clear_old_logs(days=30)` deletes idle sessions with their runs, messages, files and chunks.
- Tool results are capped separately: stored tool output is truncated to 8000 chars; one tool result may use up to half the model window (`single_tool_result_char_budget`).

## Example (hypothetical)
Model window 128k tokens -> inline threshold 256k chars. A 40k-char PDF is inlined; a 1M-char PDF is chunked and embedded, and each question pulls its 8 best chunks plus a note.

## Production notes
- Handled: stateless runs, bounded attachment injection, stale-run recovery, retention.
- Partial: token counts are estimated with 4 chars/token (not a tokenizer); history is never summarised or trimmed, so a very long session can eventually exceed the window; the injection budget counts only message text.
- Missing: conversation summarisation / sliding window; per-user cost caps.

## Checkpoint questions
1. Why is the stored transcript left unchanged by `build_prompt_messages`?
2. What happens to a large attachment when the embedding service is down, and why is the demotion in memory only?
3. Which limit stops a runaway tool loop, which stops a slow run, and which protects the context window?
4. What would break in a long session, and where would you add a fix?
