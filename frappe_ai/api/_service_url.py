# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Resolve this site's FastAPI service URL from `frappe.conf`.

`frappe_ai_service_url` defaults to the Procfile's local uvicorn bind."""

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
