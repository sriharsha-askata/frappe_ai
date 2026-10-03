# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Load FastAPI bootstrap settings without a Frappe database connection.

Read the shared secret from the site's config, merged over common site config
(ADR 0010). `FRAPPE_AI_SITE` selects the site, falling back to the bench default.
Other settings are fetched from Frappe over authenticated HTTP."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ServiceConfigError(RuntimeError):
	"""Raised when required bootstrap configuration is missing or unreadable."""


@dataclass(frozen=True)
class ServiceSettings:
	"""Startup settings: service secret, site, Frappe URL, and allowed origins."""

	service_secret: str
	site: str
	frappe_url: str
	cors_origins: list[str] = field(default_factory=list)


def _split_origins(raw: str) -> list[str]:
	"""Parse nonempty, trimmed origins from a comma-separated string."""
	return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _bench_root() -> Path:
	"""Locate the bench root from this editable app's installation path."""
	return Path(__file__).resolve().parents[4]


def _load_json(path: Path) -> dict:
	"""Load a JSON object, or `{}` for a missing file; reject invalid JSON."""
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text())
	except (OSError, json.JSONDecodeError) as e:
		raise ServiceConfigError(f"Could not read {path}: {e}") from e


def _read_site_config(sites_path: Path, site: str) -> dict:
	"""Merge common site config with site-specific overrides without Frappe init.

	Raise `ServiceConfigError` when the site or its config file is missing."""
	common_config = _load_json(sites_path / "common_site_config.json")

	site_config_path = sites_path / site / "site_config.json"
	if not site_config_path.exists():
		raise ServiceConfigError(f"{site_config_path} does not exist — is FRAPPE_AI_SITE correct?")

	config = {**common_config, **_load_json(site_config_path)}
	return config


def load_settings() -> ServiceSettings:
	"""Load startup settings or raise `ServiceConfigError` for missing site or secret."""
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
