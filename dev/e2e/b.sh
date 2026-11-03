#!/bin/bash
# usage: b.sh <bench args...>   (runs bench as the frappe user inside the e2e bench)
args=$(printf '%q ' "$@")
su frappe -c "export E2E_BASELINE=$E2E_BASELINE; source /var/tmp/e2e/env.sh; cd /var/tmp/e2e/work/frappe-bench && bench $args"
