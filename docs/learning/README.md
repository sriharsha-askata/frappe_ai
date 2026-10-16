# Learning path

A guided, module-by-module tour of `frappe_ai` for developers new to AI engineering.
Written against `origin/final-cleanup-codex`. Vocabulary follows
[dictionary-of-ai-coding](https://github.com/mattpocock/dictionary-of-ai-coding);
Agno behaviour is from the Agno docs (not verified against installed source;
`pyproject.toml` pins `agno>=2.8,<3`, `openai>=1.0,<2`).

| # | Lesson | Status |
|---|---|---|
| 0 | [Big picture](00-overview.md) | done |
| 1 | [Entry, config, run token](01-entry-config-token.md) | done |
| 2 | [How the service talks back to Frappe](02-frappe-client.md) | done |
| 3 | [Building an agent](03-builder.md) | done |
| 4 | Agent loop and streaming (`routes/chat.py`, `agno_events.py`, `transcript.py`) | next |
| 5 | Tools and dispatch (`api/dispatch.py`) | |
| 6 | Pause and approve (`routes/confirmation.py`, `AI Run.approvals`) | |
| 7 | Budgets and run state | |
| 8 | Knowledge / RAG (`knowledge/`) | |
| 9 | Memory | |
| 10 | Triggers | |
| 11 | Frontend contract | |
