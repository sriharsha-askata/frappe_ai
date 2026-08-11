# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Service bootstrap configuration.

The FastAPI process does not call `frappe.init`/`frappe.connect` (see
`001-architecture.md` §10) — all Frappe access is over HTTP, and the service never
opens a database connection of its own. That creates a bootstrap ordering problem:
the service's first call to Frappe must already be authenticated, so the shared
secret cannot itself come from that first call.

Resolution (ADR 0010, revised): the secret lives in `sites/<site>/site_config.json`
as `frappe_ai_service_secret` — the same file every other Frappe process (web,
worker, console) already reads via `frappe.conf`, so there is exactly one place the
secret is configured, not two kept in sync by hand. The FastAPI process reads that
file directly as plain JSON, merged with `sites/common_site_config.json` the same
way `frappe.get_site_config()` does — **without** `frappe.init`/`frappe.connect`,
so it never opens a database connection; `site_config.json` is just a file on disk.

The only thing still supplied at process launch is the **site name** — a name, not
a credential — via `FRAPPE_AI_SITE`, or the bench's own `default_site` from
`common_site_config.json` when unset. Everything else `AI Settings` holds
(timeouts, etc.) is fetched lazily over HTTP using the site-config secret as a
shared-secret credential — see `frappe_client.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ServiceConfigError(RuntimeError):
	"""Raised when required bootstrap configuration is missing or unreadable."""


@dataclass(frozen=True)
class ServiceSettings:
	"""Bootstrap settings read once at process startup.

	Attributes:
		service_secret (str): Shared secret from `site_config.json`'s
			`frappe_ai_service_secret`; must match what `api/service.py` reads via
			`frappe.conf.frappe_ai_service_secret` on the Frappe side (same file).
		site (str): Site name sent as `X-Frappe-Site-Name` on every Frappe call.
		frappe_url (str): Base URL of the Frappe web process.
		cors_origins (list[str]): Browser origins allowed to call this service directly.
	"""

	service_secret: str
	site: str
	frappe_url: str
	cors_origins: list[str] = field(default_factory=list)


def _split_origins(raw: str) -> list[str]:
	"""Parse a comma-separated origin list, dropping blanks and surrounding whitespace.

	Args:
		raw (str): Comma-separated origins, e.g. "http://a,http://b".

	Returns:
		list[str]: Non-empty, whitespace-trimmed origins.
	"""
	return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _bench_root() -> Path:
	"""Locate the bench root from this file's install path.

	`frappe_ai` is installed editable under `<bench>/apps/frappe_ai`, so the bench
	root is three parents up from this file (`service/config.py` ->
	`frappe_ai` package -> `frappe_ai` app dir -> `apps/` -> bench root).

	Returns:
		Path: Absolute path to the bench root.
	"""
	return Path(__file__).resolve().parents[4]


def _load_json(path: Path) -> dict:
	"""Load a JSON file, returning an empty dict if it doesn't exist.

	Args:
		path (Path): File to load.

	Returns:
		dict: Parsed JSON, or `{}` if the file is absent.

	Raises:
		ServiceConfigError: If the file exists but isn't valid JSON.
	"""
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text())
	except (OSError, json.JSONDecodeError) as e:
		raise ServiceConfigError(f"Could not read {path}: {e}") from e


def _read_site_config(sites_path: Path, site: str) -> dict:
	"""Read `site_config.json` merged over `common_site_config.json`.

	Mirrors `frappe.config.get_site_config`'s merge order (common, then
	site-specific overrides) without importing `frappe` or opening a database
	connection — this reads two files off disk, nothing more.

	Args:
		sites_path (Path): The bench's `sites/` directory.
		site (str): Site name; its config lives at `sites/<site>/site_config.json`.

	Returns:
		dict: Merged configuration.

	Raises:
		ServiceConfigError: If the site's directory or `site_config.json` is missing.
	"""
	common_config = _load_json(sites_path / "common_site_config.json")

	site_config_path = sites_path / site / "site_config.json"
	if not site_config_path.exists():
		raise ServiceConfigError(f"{site_config_path} does not exist — is FRAPPE_AI_SITE correct?")

	config = {**common_config, **_load_json(site_config_path)}
	return config


def load_settings() -> ServiceSettings:
	"""Load bootstrap settings from `site_config.json` plus a small amount of env.

	Returns:
		ServiceSettings: The process's bootstrap configuration.

	Raises:
		ServiceConfigError: When the site can't be determined, its config can't be
			read, or `frappe_ai_service_secret` is missing from it.
	"""
	sites_path = Path(os.environ.get("FRAPPE_AI_SITES_PATH", _bench_root() / "sites"))
	common_config = _load_json(sites_path / "common_site_config.json")

	site = os.environ.get("FRAPPE_AI_SITE") or common_config.get("default_site")
	if not site:
		raise ServiceConfigError(
			"Could not determine the site: set FRAPPE_AI_SITE, or set default_site "
			"in common_site_config.json."
		)

	site_config = _read_site_config(sites_path, site)

	service_secret = site_config.get("frappe_ai_service_secret")
	if not service_secret:
		raise ServiceConfigError(
			f"'frappe_ai_service_secret' is not set in sites/{site}/site_config.json"
		)

	frappe_url = os.environ.get("FRAPPE_AI_FRAPPE_URL", "http://127.0.0.1:8000")
	cors_origins = _split_origins(
		os.environ.get("FRAPPE_AI_CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
	)

	return ServiceSettings(
		service_secret=service_secret,
		site=site,
		frappe_url=frappe_url,
		cors_origins=cors_origins,
	)
