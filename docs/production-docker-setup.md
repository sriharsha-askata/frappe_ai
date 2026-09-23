# frappe_ai — Production Docker Setup

**Applies to:** `apps/frappe_ai`
**Status:** Current as of 2026-09-17

---

## 1. Overview

`frappe_ai` requires **two running processes** in production:

| Process | Default port | What it does |
|---|---|---|
| Frappe (gunicorn) | 8000 | All configuration, persistence, authorization, tool execution |
| FastAPI sidecar (uvicorn) | 8001 | Agno LLM run loop, SSE streaming to browser |

The two processes share nothing at runtime except the `sites/` volume (to read
`site_config.json`) and a short-lived HMAC shared secret. They communicate over
HTTP. The FastAPI service never opens a database connection and never holds a
credential at rest.

An additional dependency is the **Ollama embedding service** (for the knowledge/RAG
pipeline). This can run as a separate container on the same Docker network, or on a
dedicated host reachable by its internal address.

---

## 2. Environment Variables

### 2.1 Frappe backend (existing services: `backend`, `configurator`, `create-site`, workers, scheduler)

These are unchanged from the existing compose. No new env vars are needed on the
Frappe side — `frappe_ai`'s Frappe-facing configuration is stored in
`site_config.json` and read via `frappe.conf`.

### 2.2 FastAPI sidecar (`frappe-ai` service — new)

| Variable | Required | Default | Description |
|---|---|---|---|
| `FRAPPE_AI_SITE` | No (see note) | `common_site_config.json:default_site` | Site name to serve. Must match the site created in `create-site`. |
| `FRAPPE_AI_FRAPPE_URL` | Yes | `http://127.0.0.1:8000` | Internal URL of the Frappe backend. In Docker Swarm/Compose set this to the `backend` service's internal address, e.g. `http://backend:8000`. |
| `FRAPPE_AI_CORS_ORIGINS` | Yes | `http://127.0.0.1:8000` | Comma-separated list of browser origins allowed to open an SSE connection. Must include the public hostname of the Frappe frontend, e.g. `https://fact-planning-admin.asakta.com`. |
| `FRAPPE_AI_SITES_PATH` | No | Derived from install path | Absolute path to the bench `sites/` directory. Leave unset when the service runs from the same volume layout as the bench (`/home/frappe/frappe-bench/sites`). |
| `FRAPPE_AI_OLLAMA_BASE_URL` | Yes (for RAG) | `http://localhost:11434/v1` | OpenAI-compatible endpoint of the Ollama embedding service. |

> **`FRAPPE_AI_SITE` note:** If `common_site_config.json` already sets
> `default_site`, this variable can be omitted. In multi-site benches, set it
> explicitly.

### 2.3 Ollama service (`ollama` — new)

No environment variables are required beyond standard Docker configuration. The
Ollama service must have the `nomic-embed-text` model pulled before the Frappe
workers start processing knowledge sources.

---

## 3. Site Configuration (site_config.json)

Two keys are required in `sites/<SITE_NAME>/site_config.json`. They are set with
`bench set-config` commands (not in the Frappe desk UI), which means they belong in
the `create-site` or `backend` startup script.

### 3.1 `frappe_ai_service_secret`

**Required.** A random secret string shared between Frappe and the FastAPI service.
Used to authenticate service-to-service callbacks (`persist_run_result`, `fail_run`,
`dispatch_tool`).

Generate once per deployment:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Set it during site setup:

```bash
bench --site $SITE_NAME set-config frappe_ai_service_secret "<generated-secret>"
```

Pass the same value as an environment variable to the `frappe-ai` sidecar service
via the `sites/` volume (the service reads it directly from the file — no extra env
var needed).

### 3.2 `frappe_ai_service_url`

**Required in Docker.** The internal URL of the FastAPI sidecar, as seen by the
Frappe workers. The default (`http://127.0.0.1:8001`) is for single-host benches
only — in Docker, the service is on a different container.

```bash
bench --site $SITE_NAME set-config frappe_ai_service_url "http://frappe-ai:8001"
```

---

## 4. Docker Compose Changes

The following additions integrate `frappe_ai` into the existing compose file.

### 4.1 New service: `frappe-ai`

```yaml
frappe-ai:
  image: $DEPLOYMENT_NAME:$CI_COMMIT_SHA
  networks:
    - frappe_network
  depends_on:
    backend:
      condition: service_started
    create-site:
      condition: service_completed_successfully
    volume-init:
      condition: service_completed_successfully
    ollama:
      condition: service_healthy
  restart: always
  environment:
    FRAPPE_AI_SITE: ${SITE_NAME}
    FRAPPE_AI_FRAPPE_URL: "http://backend:8000"
    FRAPPE_AI_CORS_ORIGINS: "https://fact-planning-admin.asakta.com,http://backend:8000"
    FRAPPE_AI_OLLAMA_BASE_URL: "http://ollama:11434/v1"
  volumes:
    - sites:/home/frappe/frappe-bench/sites
    - logs:/home/frappe/frappe-bench/logs
    - apps:/home/frappe/frappe-bench/apps
    - env:/home/frappe/frappe-bench/env
    - config:/home/frappe/frappe-bench/config
  command:
    - bash
    - -c
    - >
      env PYTHONPATH=/home/frappe/frappe-bench/apps
      /home/frappe/frappe-bench/env/bin/uvicorn
      frappe_ai.service.main:app
      --host 0.0.0.0
      --port 8001
      --ws none
  ports:
    - "8001:8001"
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "5"
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 20s
```

### 4.2 New service: `ollama`

```yaml
ollama:
  image: ollama/ollama:latest
  networks:
    - frappe_network
  restart: always
  volumes:
    - ollama-models:/root/.ollama
  ports:
    - "11434:11434"
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
    interval: 30s
    timeout: 10s
    retries: 5
    start_period: 30s
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]   # remove this block if no GPU is available
```

> **GPU note:** Remove the `deploy.resources` block if the host has no GPU. Ollama
> runs on CPU only but will be significantly slower for large knowledge bases.

### 4.3 New service: `ollama-init`

A one-shot container that pulls the required embedding model into the shared volume
before any Frappe worker attempts to embed a knowledge source.

```yaml
ollama-init:
  image: ollama/ollama:latest
  networks:
    - frappe_network
  depends_on:
    ollama:
      condition: service_healthy
  entrypoint: ["sh", "-c"]
  command:
    - >
      OLLAMA_HOST=http://ollama:11434
      ollama pull nomic-embed-text &&
      echo "nomic-embed-text ready"
  volumes:
    - ollama-models:/root/.ollama
  deploy:
    restart_policy:
      condition: none
```

### 4.4 Additions to existing services

#### `create-site` — add `frappe_ai` installation and config

Add these lines to the `create-site` command block, after the existing
`bench --site $SITE_NAME install-app` calls and before `clear-cache`:

```bash
bench --site $SITE_NAME install-app frappe_ai;
bench --site $SITE_NAME set-config frappe_ai_service_secret "$FRAPPE_AI_SERVICE_SECRET";
bench --site $SITE_NAME set-config frappe_ai_service_url "http://frappe-ai:8001";
bench --site $SITE_NAME set-config server_script_enabled 1;
```

Add `frappe_ai_service_secret` to the `create-site` environment:

```yaml
environment:
  FRAPPE_AI_SERVICE_SECRET: ${FRAPPE_AI_SERVICE_SECRET}
  # ... existing vars ...
```

#### `backend` — add `frappe_ai` git pull and dependency install

Add this inside the existing `bench use $SITE_NAME; cd apps/...` block:

```bash
cd ../frappe_ai;
git pull;
bench pip install agno openai fastapi uvicorn httpx lancedb python-docx pdfplumber rapidocr onnxruntime openpyxl beautifulsoup4 lxml chardet croniter mcp litellm RestrictedPython;
```

> **Note:** In a properly built Docker image (`$DEPLOYMENT_NAME:$CI_COMMIT_SHA`)
> all Python dependencies should already be baked in via `bench setup requirements`.
> The `pip install` line above is only needed if the image is rebuilt without
> running `bench setup requirements --dev` for `frappe_ai`.

#### `backend` — add `frappe_ai_service_url` config set on each deploy

Inside the backend's startup command, after `bench --site $SITE_NAME migrate`:

```bash
bench --site $SITE_NAME set-config frappe_ai_service_url "http://frappe-ai:8001";
```

This is safe to run on every deploy and ensures the URL survives a site config reset.

### 4.5 New volume

Add `ollama-models` to the `volumes:` section at the bottom of the compose file:

```yaml
volumes:
  # ... existing volumes ...
  ollama-models:
```

---

## 5. Required Environment Variables (`.env` additions)

```dotenv
# frappe_ai sidecar shared secret — generate once:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
FRAPPE_AI_SERVICE_SECRET=<generated-32-byte-hex>
```

---

## 6. System Package Requirements

The `frappe_ai` OCR/PDF pipeline requires two system libraries that must be present
in the Docker image:

```text
libgl1
libglib2.0-0
```

These are declared in `pyproject.toml` under `[deploy.dependencies.apt]`. If your
image uses `frappe_docker`'s standard `apps.json`-based build, they are included
automatically. If the image is built manually, add to the `Dockerfile`:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
```

---

## 7. LanceDB Storage

LanceDB stores vector embeddings under:

```
sites/<SITE_NAME>/private/files/lancedb/
```

This path lives inside the `sites` volume, which is already persisted by the
existing compose. No additional volume is needed.

**Backup:** Include `sites/<SITE_NAME>/private/files/lancedb/` in your backup
alongside MariaDB. LanceDB is a disposable derived index (ADR 0017) — it can be
rebuilt from MariaDB — but the rebuild is expensive for large knowledge bases, so
backing it up avoids downtime after a volume loss.

**Rebuild (if the LanceDB index is lost or corrupted):**

```python
# Run from a Frappe console: bench --site <site> console
from frappe_ai.knowledge.migration import rebuild_knowledge_index
rebuild_knowledge_index()
```

---

## 8. Complete Annotated compose.yml Diff

Below is the complete diff of additions and changes to apply to the existing
compose file. Unchanged services are omitted.

```yaml
services:

  # ── CHANGED: backend ─────────────────────────────────────────────────────
  backend:
    # ... existing config unchanged ...
    command:
      - bash
      - -c
      - >
        pwd;
        ( bench use $SITE_NAME;
        cd apps/frappe;
        git pull;
        cd ../erpnext;
        git pull;
        cd ../dfp_external_storage;
        git pull;
        cd ../factory_automation_api;
        git reset --hard;
        git clean -fd;
        git pull;
        cd ../erpnext_extensions;
        git pull;
        cd ../frappe_extensions;
        git fetch upstream;
        git reset --hard upstream/dev;
        git pull;
        cd ../frappe_utils;
        git pull;
        cd ../frappe_ai;
        git pull;
        cd ../factory_automation_api/fact-frontend;
        echo $NPM_TOKEN
        npm i;
        npm install @tailwindcss/vite --save-dev;
        npm run build;
        echo "Migration Start";
        bench --site $SITE_NAME migrate;
        bench --site $SITE_NAME set-config frappe_ai_service_url "http://frappe-ai:8001";
        echo "Migration Complete";
        echo "Building for Production";
        bench build --production && env PYTHONPATH=/home/frappe/frappe-bench/apps /home/frappe/frappe-bench/env/bin/gunicorn --chdir=/home/frappe/frappe-bench/sites --bind=0.0.0.0:8000 --threads=4 --workers=9 --worker-class=gthread --worker-tmp-dir=/dev/shm --timeout=120 --preload frappe.app:application ) > >(tee -a /home/frappe/frappe-bench/logs/gunicorn.log) 2>&1

  # ── CHANGED: create-site ─────────────────────────────────────────────────
  create-site:
    environment:
      FRAPPE_AI_SERVICE_SECRET: ${FRAPPE_AI_SERVICE_SECRET}
      # ... existing env vars unchanged ...
    command:
      - >
        ( export start=$(date +%s);
        until [[ -n $(grep -hs ^ sites/common_site_config.json | jq -r ".db_host // empty") ]] &&
              [[ -n $(grep -hs ^ sites/common_site_config.json | jq -r ".redis_cache // empty") ]] &&
              [[ -n $(grep -hs ^ sites/common_site_config.json | jq -r ".redis_queue // empty") ]];
        do
           sleep 5;
        done;
        bench pip install dropbox;
        bench new-site $SITE_NAME --db-host=ssel-hrms.cb2y82iiehfp.ap-south-1.rds.amazonaws.com --admin-password="$ADMIN_PASSWORD" --db-root-password="$DB_ROOT_PASSWORD" --db-user=factplan --db-name=factplan --db-password="$DB_PASSWORD";
        bench --site $SITE_NAME set-config allow_cors '*';
        echo "Site Created Successfully";
        bench --site $SITE_NAME clear-cache;
        bench --site $SITE_NAME set-config host_name "https://fact-planning-admin.asakta.com";
        bench --site $SITE_NAME install-app frappe_extensions;
        bench --site $SITE_NAME install-app erpnext;
        bench --site $SITE_NAME install-app erpnext_extensions;
        bench --site $SITE_NAME install-app factory_automation_api;
        bench --site $SITE_NAME install-app dfp_external_storage;
        bench --site $SITE_NAME install-app frappe_utils;
        bench --site $SITE_NAME install-app frappe_ai;
        bench --site $SITE_NAME set-config frappe_ai_service_secret "$FRAPPE_AI_SERVICE_SECRET";
        bench --site $SITE_NAME set-config frappe_ai_service_url "http://frappe-ai:8001";
        bench --site $SITE_NAME set-config server_script_enabled 1;
        bench --site $SITE_NAME clear-cache; ) > >(tee -a /home/frappe/frappe-bench/logs/create-site.log) 2>&1

  # ── NEW: frappe-ai sidecar ────────────────────────────────────────────────
  frappe-ai:
    image: $DEPLOYMENT_NAME:$CI_COMMIT_SHA
    networks:
      - frappe_network
    depends_on:
      backend:
        condition: service_started
      create-site:
        condition: service_completed_successfully
      volume-init:
        condition: service_completed_successfully
      ollama:
        condition: service_healthy
    restart: always
    environment:
      FRAPPE_AI_SITE: ${SITE_NAME}
      FRAPPE_AI_FRAPPE_URL: "http://backend:8000"
      FRAPPE_AI_CORS_ORIGINS: "https://fact-planning-admin.asakta.com,http://backend:8000"
      FRAPPE_AI_OLLAMA_BASE_URL: "http://ollama:11434/v1"
    volumes:
      - sites:/home/frappe/frappe-bench/sites
      - logs:/home/frappe/frappe-bench/logs
      - apps:/home/frappe/frappe-bench/apps
      - env:/home/frappe/frappe-bench/env
      - config:/home/frappe/frappe-bench/config
    command:
      - bash
      - -c
      - >
        env PYTHONPATH=/home/frappe/frappe-bench/apps
        /home/frappe/frappe-bench/env/bin/uvicorn
        frappe_ai.service.main:app
        --host 0.0.0.0
        --port 8001
        --ws none
    ports:
      - "8001:8001"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s

  # ── NEW: ollama embedding service ─────────────────────────────────────────
  ollama:
    image: ollama/ollama:latest
    networks:
      - frappe_network
    restart: always
    volumes:
      - ollama-models:/root/.ollama
    ports:
      - "11434:11434"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"

  # ── NEW: one-shot model pull ──────────────────────────────────────────────
  ollama-init:
    image: ollama/ollama:latest
    networks:
      - frappe_network
    depends_on:
      ollama:
        condition: service_healthy
    entrypoint: ["sh", "-c"]
    command:
      - >
        OLLAMA_HOST=http://ollama:11434
        ollama pull nomic-embed-text &&
        echo "nomic-embed-text ready"
    volumes:
      - ollama-models:/root/.ollama
    deploy:
      restart_policy:
        condition: none

volumes:
  db-data:
  sites:
  assets:
  logs:
  ssh:
  apps:
  env:
  config:
  ollama-models:   # ← new

networks:
  frappe_network:
    driver: bridge
    name: frappe_network
```

---

## 9. nginx Proxy (frontend service)

The existing `frontend` service uses `nginx-entrypoint.sh`. If the `frappe-ai`
sidecar's SSE endpoint needs to be reachable via the same public hostname (i.e., to
avoid CORS entirely), proxy `/api/ai/stream/` to the sidecar:

```nginx
# Add inside the nginx server block, before the main Frappe proxy pass
location /api/ai/stream/ {
    proxy_pass http://frappe-ai:8001/stream/;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_set_header Cache-Control 'no-cache';
    proxy_set_header X-Accel-Buffering 'no';
    proxy_buffering off;
    proxy_read_timeout 620s;    # > stream_timeout (600s) + margin
    proxy_send_timeout 620s;
    chunked_transfer_encoding on;
}
```

If the nginx config is not customizable in `nginx-entrypoint.sh`, the simpler path
is to expose port 8001 from the `frappe-ai` container directly and set
`FRAPPE_AI_CORS_ORIGINS` to the browser's public origin — the architecture already
supports this (the browser opens the SSE connection directly to FastAPI).

---

## 10. First-Run Desk Configuration

After `bench migrate` completes and the services are up:

1. **Open the Frappe desk** and navigate to **Frappe AI → AI Settings**.
   Confirm `Service Status` shows `ok` (the health probe runs every 5 minutes via
   the scheduler).

2. **Create an AI Provider** (Frappe AI → AI Provider):
   - Set `Provider` to your LLM provider slug (e.g. `openai`, `anthropic`, `groq`).
   - Enter the `API Key`.
   - Leave `Base URL` blank for providers with a standard endpoint (see
     `lib/model.py:PROVIDER_ENDPOINT_DEFAULTS`); fill it in for self-hosted or
     custom providers.

3. **Create an AI Model** (Frappe AI → AI Model):
   - Set `Model ID` to the provider's model name (e.g. `claude-sonnet-4-6`,
     `gpt-4o`, `llama3`).
   - Link the `AI Provider`.
   - Check `Enabled` and optionally `Is Default`.
   - Set `Context Window` to the model's actual token limit (used to calculate safe
     tool result payload sizes).

4. **Create an AI Agent** (Frappe AI → AI Agent):
   - Write the system `Instructions`.
   - Bind tools, knowledge bases, and MCP connections as needed.
   - Check `Enabled`.

5. **Verify the embedding pipeline** by creating an AI Knowledge Base and adding a
   Text source. Check that the source status reaches `Indexed`. If it fails, inspect
   Frappe Error Log for embedding errors and confirm Ollama is reachable at the
   configured `FRAPPE_AI_OLLAMA_BASE_URL`.

---

## 11. Upgrading an Existing Installation

Before every deploy:

1. **Back up MariaDB** and `sites/<SITE_NAME>/private/files/lancedb/`.
2. **Pull `frappe_ai`** (`cd apps/frappe_ai && git pull`).
3. **Run `bench migrate`** — `after_migrate` hooks sync builtin tools and run
   schema patches automatically.

If the Ollama model digest or embedding dimension changes between versions, run the
explicit rebuild from a Frappe console **before** serving knowledge search traffic:

```python
from frappe_ai.knowledge.migration import rebuild_knowledge_index
rebuild_knowledge_index()
```

---

## 12. Production Readiness Checklist

Based on the 2026-09-10 production readiness review:

### Cleared to deploy (no MCP connections)

- [x] `frappe_ai_service_secret` set in `site_config.json`
- [x] `frappe_ai_service_url` pointing to the sidecar's internal Docker address
- [x] `FRAPPE_AI_FRAPPE_URL` pointing to the Frappe backend's internal Docker address
- [x] `FRAPPE_AI_CORS_ORIGINS` includes the public frontend hostname
- [x] Ollama running and `nomic-embed-text` pulled
- [x] `libgl1` and `libglib2.0-0` present in the Docker image
- [x] `sites/` volume is backed up (includes LanceDB)
- [x] `frappe-ai` service has a healthcheck; load balancer / reverse proxy only
  routes after it passes
- [x] `auto_approve` triggers remain disabled on production agents

### Required before enabling MCP connections on production agents

- [ ] F-11 — MCP tool calls counted against execution budgets (open; see
  `docs/to_do/high-mcp-budget-bypass.md`)
- [ ] F-13 — Tool arguments and prompt context removed from Error Log (open)

Until F-11 is resolved, do not bind `AI MCP Connection` rows to agents that run
against production data. The security model for MCP connections uses a shared
connection identity (not the acting user's permissions) — see ADR 0019.

---

## 13. Observability

| Signal | Location |
|---|---|
| Frappe web/worker logs | `logs/gunicorn.log`, `logs/worker.log` |
| FastAPI sidecar logs | `logs/` (json-file driver, same volume) |
| Agno run errors | Frappe Error Log (desk: Error Log list) |
| LLM call diagnostics | Frappe Error Log (title: `frappe_ai Run <name>: Agno error`) |
| Service health | `GET http://frappe-ai:8001/health` returns `{"status":"ok","frappe_reachable":bool}` |
| AI Settings desk page | Shows `Service Status` (updated by the every-5-minute scheduler cron) |

No correlation ID currently ties a run's Frappe-side and FastAPI-side logs. This is
a known gap (tracked in `docs/to_do/medium-correlation-id.md`). To trace a run
across both processes, search Frappe Error Log for the `AI Run` name.
