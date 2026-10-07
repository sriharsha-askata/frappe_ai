# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Authenticated async HTTP client for the FastAPI service's Frappe calls.

Use `X-Frappe-AI-Service-Secret`, since Frappe intercepts standard Bearer
`Authorization` headers before whitelisted methods run. `X-Frappe-Site-Name`
selects the site. Run configuration and dispatch pass the acting user explicitly
so Frappe applies that user's permissions."""

from __future__ import annotations

from typing import Any

import httpx

from frappe_ai.service.config import ServiceSettings

GET_SERVICE_CONFIG_METHOD = "frappe_ai.api.service.get_service_config"
GET_RUN_CONFIG_METHOD = "frappe_ai.api.service.get_run_config"
GET_FALLBACK_MODEL_CONFIG_METHOD = "frappe_ai.api.service.get_fallback_model_config"
DISPATCH_PLUGIN_TOOL_METHOD = "frappe_ai.api.dispatch.dispatch_plugin_tool"
PERSIST_RUN_RESULT_METHOD = "frappe_ai.api.api.persist_run_result"
FAIL_RUN_METHOD = "frappe_ai.api.api.fail_run"
LOG_DIAGNOSTIC_METHOD = "frappe_ai.api.api.log_diagnostic"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
TOOL_DISPATCH_TIMEOUT_SECONDS = 300.0


class FrappeClientError(Exception):
	"""Raised when a call to Frappe fails or returns an unexpected shape."""


class FrappeClient:
	"""Authenticated async client for FastAPI → Frappe calls.

	Attributes:
		settings (ServiceSettings): Bootstrap settings (secret, site, base URL).
	"""

	def __init__(self, settings: ServiceSettings, timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS):
		"""
		Args:
			settings (ServiceSettings): Bootstrap settings this client authenticates with.
			timeout (float): Per-request timeout in seconds for config/control calls.
				Tool dispatch uses a separate longer timeout because Frappe-side tools
				can do OCR/PDF extraction.
		"""
		self.settings = settings
		self._timeout = timeout

	def _headers(self) -> dict[str, str]:
		"""Build the auth headers sent on every request.

		Returns:
			dict[str, str]: `X-Frappe-AI-Service-Secret` and `X-Frappe-Site-Name` headers.
		"""
		return {
			"X-Frappe-AI-Service-Secret": self.settings.service_secret,
			"X-Frappe-Site-Name": self.settings.site,
		}

	async def get_service_config(self) -> dict[str, Any]:
		"""Fetch service settings from Frappe using the shared secret."""
		return await self._get_json(GET_SERVICE_CONFIG_METHOD)

	async def ping(self) -> bool:
		"""Check whether Frappe is reachable and accepts this service's secret.

		Returns:
			bool: True if `get_service_config` succeeds, False otherwise.
		"""
		try:
			await self.get_service_config()
			return True
		except FrappeClientError:
			return False

	async def get_run_config(self, run: str, user: str) -> dict[str, Any]:
		"""Fetch the agent, model, tools, and prompt for one run as `user`."""
		return await self._get_json(GET_RUN_CONFIG_METHOD, params={"run": run, "user": user})

	async def get_fallback_model_config(self, user: str) -> dict[str, Any]:
		"""Fetch an enabled default model for a provider-error retry as `user`."""
		return await self._get_json(GET_FALLBACK_MODEL_CONFIG_METHOD, params={"user": user})

	async def dispatch_plugin_tool(
		self,
		tool: str,
		user: str,
		arguments: dict[str, Any] | None = None,
		run: str | None = None,
		call_id: str | None = None,
	) -> dict[str, Any]:
		"""Execute a same-site Assistant Core tool through its registry."""
		return await self._post_json(
			DISPATCH_PLUGIN_TOOL_METHOD,
			json={"tool": tool, "user": user, "arguments": arguments or {}, "run": run, "call_id": call_id},
			timeout=TOOL_DISPATCH_TIMEOUT_SECONDS,
		)

	async def persist_run_result(self, run: str, result: dict[str, Any]) -> dict[str, Any]:
		"""Persist a completed or paused run segment and return its status."""
		return await self._post_json(PERSIST_RUN_RESULT_METHOD, json={"run": run, "result": result})

	async def fail_run(self, run: str, error: str) -> dict[str, Any]:
		"""Mark a run Failed after an exception or pre-completion disconnect."""
		return await self._post_json(FAIL_RUN_METHOD, json={"run": run, "error": error})

	async def log_diagnostic(self, title: str, message: str) -> None:
		"""Best-effort Error Log callback; logging failures never mask run failures."""
		try:
			await self._post_json(LOG_DIAGNOSTIC_METHOD, json={"title": title, "message": message})
		except FrappeClientError:
			pass

	async def _get_json(self, method: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
		url = f"{self.settings.frappe_url}/api/method/{method}"
		try:
			async with httpx.AsyncClient(timeout=self._timeout) as client:
				response = await client.get(url, headers=self._headers(), params=params)
		except httpx.HTTPError as e:
			raise FrappeClientError(f"Could not reach Frappe at {url}: {e}")
		return self._unwrap(url, response)

	async def _post_json(
		self, method: str, *, json: dict[str, Any], timeout: float | None = None
	) -> dict[str, Any]:
		url = f"{self.settings.frappe_url}/api/method/{method}"
		try:
			async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
				response = await client.post(url, headers=self._headers(), json=json)
		except httpx.HTTPError as e:
			raise FrappeClientError(f"Could not reach Frappe at {url}: {e}")
		return self._unwrap(url, response)

	def _unwrap(self, url: str, response: httpx.Response) -> dict[str, Any]:
		if response.status_code == 401:
			raise FrappeClientError("Frappe rejected the service secret (401)")
		if response.status_code != 200:
			raise FrappeClientError(f"Unexpected status {response.status_code} from {url}: {response.text[:500]}")

		payload = response.json()
		# Whitelisted methods wrap the return value in {"message": ...}.
		return payload.get("message", payload)
