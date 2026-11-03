#!/bin/bash
# usage: r.sh <site> <module.function> [json-kwargs]   run a function inside a site (as Administrator)
args=$(printf '%q ' "$@")
su frappe -c "export E2E_BASELINE=$E2E_BASELINE; source /var/tmp/e2e/env.sh; cd /var/tmp/e2e/work/frappe-bench/sites && ../env/bin/python /home/user/frappe_ai/dev/e2e/run_in_site.py $args"
