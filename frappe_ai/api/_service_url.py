# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Resolve the FastAPI service base URL for the current site.

Read exclusively via the standard Frappe conf accessor (`frappe.conf`,
i.e. `frappe.get_conf()`). The FastAPI *service* process has its own
file-based loader in `frappe_ai/service/config.py` because that process
never calls `frappe.init`/`frappe.connect` (ADR 0011, architecture §10) —
this is the Frappe-side counterpart, where Frappe has already loaded
`site_config.json` into `frappe.conf` at process start.

Operator sets `frappe_ai_service_url` in `sites/<site>/site_config.json`.
Default matches the Procfile's uvicorn bind (`http://127.0.0.1:8001`) so
single-host dev benches need no config.
"""

from __future__ import annotations

import frappe

DEFAULT_SERVICE_URL = "http://127.0.0.1:8001"


def get_service_url() -> str:
	"""Return the FastAPI service base URL for the current site.

	Reads `frappe.conf.frappe_ai_service_url`. Falls back to
	`http://127.0.0.1:8001` when unset. The result is stripped of any
	trailing slash so callers can do `f"{url}/health"` without double
	slashes.
	"""
	url = (frappe.conf.get("frappe_ai_service_url") or "").strip().rstrip("/")
	return url or DEFAULT_SERVICE_URL
