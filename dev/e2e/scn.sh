#!/bin/bash
# usage: scn.sh <site> <service-port> [--only names...]   (run as root; calls r.sh itself)
cd /var/tmp/e2e/work/frappe-bench && /var/tmp/e2e/work/frappe-bench/env/bin/python /home/user/frappe_ai/dev/e2e/run_scenarios.py "$@"
