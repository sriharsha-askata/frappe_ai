# Frappe AI setup

## Ollama embeddings

`frappe_ai` uses one application-wide embedding model:

- provider: Ollama
- model: `nomic-embed-text`

Install that exact model on the Ollama host:

```bash
ollama pull nomic-embed-text
```

Keep Ollama's model directory on persistent storage. Configure the OpenAI-compatible
endpoint in the environment available to Frappe web/workers and the FastAPI process:

```bash
FRAPPE_AI_OLLAMA_BASE_URL=http://localhost:11434/v1
```

For a separate production host, use an internal address such as:

```bash
FRAPPE_AI_OLLAMA_BASE_URL=http://ollama.internal:11434/v1
```

The URL is deployment configuration only. `frappe_ai` does not use it as a liveness
decision: Ollama availability is verified lazily by a real embedding request. Normal
chat does not depend on that request. If it fails, oversized chat attachments are
demoted to their capped inline representation; knowledge ingestion marks the source
failed and knowledge search reports the embedding-service error.

Do not expose Ollama publicly. Restrict access to the Frappe workers and FastAPI service
with network policy, and use the Ollama host's health checks and resource monitoring.
Size CPU/GPU, memory, and request concurrency for the corpus and ingestion queue; a
single embedding worker should write the LanceDB index at a time.

## Storage and backups

MariaDB is authoritative for knowledge text and metadata. LanceDB is a disposable,
site-scoped derived index at:

```text
sites/<site>/private/files/lancedb/
```

Back up MariaDB and this LanceDB directory. Preserve the single-writer ingestion
arrangement: Frappe workers perform index writes while the FastAPI service and other
readers search the index.

## Existing installations

Before changing an existing installation, back up MariaDB and LanceDB, deploy Ollama,
and pull `nomic-embed-text`. Then run the explicit rebuild from a Frappe console:

```python
from frappe_ai.knowledge.migration import rebuild_knowledge_index

rebuild_knowledge_index()
```

The rebuild probes the model, records its vector dimension, creates a fresh `chunks`
index, re-embeds every `AI Knowledge Chunk`, verifies MariaDB/LanceDB row counts and
index metadata, and only then removes the obsolete embedding-model setting. Rebuild
temporary `chat_attachment_chunks` vectors as users attach files again.

If the Ollama model digest or returned vector dimension changes, repeat the backup and
rebuild procedure before serving retrieval traffic. A dimension or model-identity
mismatch is rejected rather than silently mixing vector spaces.
