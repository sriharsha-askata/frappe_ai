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

## Site configuration

Two keys live in `sites/<site>/site_config.json` (ADR 0011) and configure the
FastAPI service from the Frappe side. They are deployment configuration, not
desk-editable DocType fields:

- `frappe_ai_service_secret` — shared HMAC secret. Required. Set with
  `bench --site <site> set-config frappe_ai_service_secret <value>`.
- `frappe_ai_service_url` — base URL of the FastAPI service. Optional; defaults
  to `http://127.0.0.1:8001` (matching the Procfile's `uvicorn --port 8001`).
  Set with `bench --site <site> set-config frappe_ai_service_url <value>` if
  the service is reachable under a different host or port.

The Frappe side reads both via `frappe.conf` (`frappe_ai/api/_service_url.get_service_url()`
for the URL); no file path code is touched on the Frappe side. The FastAPI
service reads the same files directly off disk in `frappe_ai/service/config.py`
because that process never calls `frappe.init`/`frappe.connect`.

## Databases

`frappe_ai` runs on MariaDB and on Postgres (ADR 0021). Use whichever your Frappe site uses.
On Postgres, install the `pgvector` extension on the database server and run
`CREATE EXTENSION vector;` once as a privileged role; managed Postgres services usually offer it.

## Vector backend

Where search vectors are stored is chosen in **AI Settings → Vector Backend** (ADR 0022):

| Value | Meaning |
|---|---|
| `Auto` (default) | pgvector on a Postgres site, LanceDB otherwise |
| `pgvector` | Vectors in the site's Postgres database. Needs the extension above. |
| `lancedb` | Embedded files under `sites/<site>/private/files/lancedb/` |
| `chroma` | Experimental. Files under `sites/<site>/private/files/chroma/`, or a Chroma server given by the `frappe_ai_chroma_url` site-config key (for example `chroma.internal:8000`). It has no keyword ranking, so hybrid search falls back to vector search. |
| other | A backend an installed app registers with the `frappe_ai_vector_backends` hook |

After changing the backend, rebuild the index (see below). Install the Chroma client only if you use it: `pip install "frappe_ai[chroma]"` (or `bench pip install chromadb`). The vector contract tests need `frappe_ai[dev]` (the `psycopg` driver).

## Audio uploads

Audio files (mp3, wav, m4a, ogg, flac, webm, mp4, mpeg, mpga) are turned into text and then used like any
other attachment or knowledge source (ADR 0024). The feature is **off** until you choose a model:

1. Create an `AI Provider` and an `AI Model` for a speech-to-text model served by an OpenAI-compatible
   `/audio/transcriptions` endpoint (OpenAI `whisper-1`, Groq `whisper-large-v3`, a local faster-whisper
   server, LocalAI). Ollama does not serve this endpoint.
2. Open **AI Settings → Audio** and select it as **Transcription Model**. Audio is sent to that provider.
3. Optionally adjust **Max Audio Size** (default 25 MB, the OpenAI limit) and **Transcription Timeout**
   (default 110 s; keep it under your web server's request timeout, 120 s by default).

Files over the limit are refused before anything is sent. Transcription happens while the file uploads.

## Storage and backups

The site database is authoritative for knowledge text and metadata. The search index is a
disposable copy that can always be rebuilt from it:

* pgvector: tables named `ai_vec_*` in the site database, covered by the normal database backup.
* LanceDB: `sites/<site>/private/files/lancedb/`. Back it up or rebuild it after a restore.
* Chroma: `sites/<site>/private/files/chroma/` or your Chroma server.

Index writes come from Frappe workers; the FastAPI service and other readers only search.
Keep a single writer per LanceDB directory.

## Rebuilding the index

Rebuild after switching the vector backend, changing the Ollama model, or restoring a
database without its index. Deploy Ollama and pull `nomic-embed-text` first, then run:

```bash
bench --site <site> execute frappe_ai.knowledge.reindex.rebuild_knowledge_index
```

The rebuild probes the model for its vector width, replaces the knowledge index, re-embeds
every `AI Knowledge Chunk`, and verifies the row count and the stored embedding identity. It also
clears the chat-attachment index, which refills as users attach files again. An index built with
a different model or width is rejected instead of silently mixing vector spaces, so rebuild
before serving retrieval traffic after any such change.
