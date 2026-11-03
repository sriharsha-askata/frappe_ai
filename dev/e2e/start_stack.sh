#!/bin/bash
# Start web, worker and one FastAPI service per site. Tracks pids in /var/tmp/e2e/pids.
cd /var/tmp/e2e; mkdir -p pids
for f in pids/*; do [ -f "$f" ] && kill "$(cat $f)" 2>/dev/null; rm -f "$f"; done; sleep 1
OLL=http://127.0.0.1:11434/v1
launch() { # name, log, command
  setsid nohup su frappe -c "source /var/tmp/e2e/env.sh; export FRAPPE_AI_OLLAMA_BASE_URL=$OLL; $3" > "$2" 2>&1 < /dev/null &
  echo $! > pids/$1
}
B="cd /var/tmp/e2e/work/frappe-bench"
launch web web.log "$B && exec bench serve --port 8000"
launch worker worker.log "$B && exec bench worker --queue short,default,long"
launch svc8001 service-8001.log "$B && FRAPPE_AI_SITE=maria.local FRAPPE_AI_SITES_PATH=/var/tmp/e2e/work/frappe-bench/sites FRAPPE_AI_FRAPPE_URL=http://127.0.0.1:8000 exec env/bin/python -m uvicorn frappe_ai.service.main:app --port 8001"
launch svc8002 service-8002.log "$B && FRAPPE_AI_SITE=pg.local FRAPPE_AI_SITES_PATH=/var/tmp/e2e/work/frappe-bench/sites FRAPPE_AI_FRAPPE_URL=http://127.0.0.1:8000 exec env/bin/python -m uvicorn frappe_ai.service.main:app --port 8002"
for i in $(seq 1 60); do curl -s -o /dev/null -H 'X-Frappe-Site-Name: maria.local' http://127.0.0.1:8000/api/method/ping && break; sleep 1; done
echo "web: $(curl -s -H 'X-Frappe-Site-Name: maria.local' http://127.0.0.1:8000/api/method/ping)"
for p in 8001 8002; do echo "service $p: $(curl -s http://127.0.0.1:$p/health)"; done
