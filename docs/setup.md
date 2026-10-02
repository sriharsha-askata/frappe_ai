# Setup

How to install `frappe_ai`, run its two processes, and configure embeddings. For what the pieces are, read [001 Architecture](specifications/001-architecture.md) first.

## 1. Install the app

You need a Frappe bench (v15) and Python 3.10 or newer.

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench --site <site> install-app frappe_ai
bench --site <site> migrate
```

`migrate` also runs the app's data patches and syncs the built-in tools.

## 2. Set the shared secret

The FastAPI service and Frappe prove who they are to each other with one secret string. Put it in the site's config:

```bash
bench --site <site> set-config frappe_ai_service_secret "$(openssl rand -hex 32)"
```

It is stored in `sites/<site>/site_config.json`. Frappe reads it through `frappe.conf`; the service reads the same file from disk. Do not copy it into the database or an environment variable ([ADR 0011](decisions/0011-service-secret-in-site-config.md)). Anyone who has this secret can call the service-facing endpoints, so treat it like a password.

## 3. Run the FastAPI service

The service must run next to Frappe, started from the bench's Python environment:

```bash
uvicorn frappe_ai.service.main:app --port 8001
```

The usual way is to add this line to the bench's `Procfile` so `bench start` runs it with everything else:

```
ai: uvicorn frappe_ai.service.main:app --port 8001
```

(The `Procfile` belongs to the bench, not to this repo, so you add the line yourself.)

On startup the service finds the site and reads the secret. It looks at these environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `FRAPPE_AI_SITE` | the bench's `default_site` | Which site's `site_config.json` to read |
| `FRAPPE_AI_SITES_PATH` | `<bench>/sites` | Location of the `sites` folder |
| `FRAPPE_AI_FRAPPE_URL` | `http://127.0.0.1:8000` | Where the service reaches Frappe |
| `FRAPPE_AI_CORS_ORIGINS` | `http://127.0.0.1:8000,http://localhost:8000` | Browser origins allowed to call the service |

If it fails to start with "frappe_ai_service_secret is not set", repeat step 2. A quick check:

```bash
curl http://127.0.0.1:8001/health      # {"status": "ok", "frappe_reachable": true}
```

Do not expose port 8001 to the public internet. The browser must be able to reach it (same host or through a reverse-proxy route), but only for the streaming call, which is protected by the run token.

## 4. Point Frappe at the service

Open **AI Settings** in the Desk. The defaults are fine for local work:

| Field | Default | Meaning |
|---|---|---|
| Service Base URL | `http://127.0.0.1:8001` | Where Frappe and the browser reach the service |
| Request Timeout | 120 s | Non-streaming requests to the service |
| Stream Timeout | 600 s | Maximum wait on a streaming connection |
| Search Type | Hybrid | Knowledge search uses vectors plus keywords (or vectors only) |
| Chunk Size / Overlap | 1000 / 200 | How documents are split for knowledge search |

## 5. Add a provider, a model and an agent

1. **AI Provider**: pick a provider and add its API key (or leave the provider empty on the model and give the model its own key and base URL).
2. **AI Model**: choose the provider, the model id, and enable it. Use the *Test Connection* button to run the capability checks ([spec 011](specifications/011-ai-model-capability-testing.md)).
3. **AI Agent**: choose the model, write the instructions, and add tools and knowledge bases.

The built-in tools are created on `migrate`.

## 6. Build the frontend (only if you change it)

The built files are committed under `frappe_ai/public/frappe_ai_panel/`. To change the UI:

```bash
cd apps/frappe_ai/frontend
npm install
npm run build        # or: npm run dev   (rebuilds on change)
```

Then hard-refresh the Desk.

## Embeddings (Ollama)

Knowledge search needs one embedding model. The app uses a single fixed model so all vectors live in the same space:

- provider: Ollama
- model: `nomic-embed-text`

On the Ollama host:

```bash
ollama pull nomic-embed-text
```

Tell the app where Ollama is, in the environment of Frappe's web and worker processes **and** the FastAPI service:

```bash
FRAPPE_AI_OLLAMA_BASE_URL=http://localhost:11434/v1
# or for a separate host:
FRAPPE_AI_OLLAMA_BASE_URL=http://ollama.internal:11434/v1
```

Keep Ollama's model directory on persistent storage, and do not expose Ollama publicly: allow only the Frappe workers and the service to reach it.

The app does not "ping" Ollama. It finds out when it makes a real embedding request. If Ollama is down:

- normal chat keeps working;
- oversized chat attachments fall back to being inserted into the prompt (shortened) instead of being searched;
- knowledge ingestion marks the source Failed, and knowledge search reports the embedding error.

Size CPU/GPU and memory for your document volume. Let only one worker write the knowledge index at a time (ingestion jobs already run on Frappe's background workers).

## Storage and backups

- **MariaDB** holds the truth: chunk text and metadata (`AI Knowledge Chunk`), plus agents, sessions and runs.
- **LanceDB** holds search vectors and keyword indexes in `sites/<site>/private/files/lancedb/`. It can be rebuilt from MariaDB.

Back up both. If you lose the LanceDB folder, rebuild it (below).

## Upgrading or rebuilding the knowledge index

Do this when moving an older install to the fixed Ollama model, after an Ollama model or vector-size change, or after losing the LanceDB folder. First back up MariaDB and LanceDB, deploy Ollama and pull the model. Then, from a Frappe console:

```python
from frappe_ai.knowledge.migration import rebuild_knowledge_index

rebuild_knowledge_index()
```

The rebuild checks the model, records its vector size, creates a fresh index, re-embeds every `AI Knowledge Chunk`, verifies the row counts, and only then removes the obsolete embedding setting. Attachment vectors are temporary; they are recreated when users attach files again.

If the model or vector size changes, the app refuses to mix old and new vectors until you rebuild.

## Optional: faster PDF extraction

By default PDFs are read with `pdfplumber` plus RapidOCR for scanned pages. If Docling is installed it is tried first, and the default extractor is the fallback:

```bash
bench pip install "docling>=1.0.0"
```

## Upgrade notes for this version

`bench migrate` applies these automatically; they matter if you read the database directly.

- `AI Run` has two new hidden fields: `approvals` (the user's recorded approvals) and `segment_started_at` (start of the current active period, used by the runtime limit).
- `AI MCP Connection` fields are validated and normalized: `command` holds only the executable, arguments live in `command_args`, and `api_key` is now a Password field. A one-off patch (`patches/normalize_mcp_connections.py`) converts existing rows and encrypts stored keys. It is temporary and can be deleted once all sites have migrated.
