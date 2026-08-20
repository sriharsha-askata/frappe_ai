# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Authenticated async HTTP client for FastAPI's calls back into Frappe.

Every call carries two things Frappe needs to authenticate and route it:
- `X-Frappe-AI-Service-Secret: <service_secret>` — proves the call comes from the
  service process (see ADR 0010; verified server-side against
  `site_config.json`'s `frappe_ai_service_secret`). Deliberately **not** the standard `Authorization`
  header: Frappe core's `validate_auth()` intercepts any `Authorization: Bearer ...`
  header itself (treating it as an OAuth bearer token) and raises
  `AuthenticationError` before a whitelisted method's body ever runs if that
  validation fails — even one decorated `allow_guest=True`. A dedicated header name
  avoids colliding with that global auth path entirely.
- `X-Frappe-Site-Name: <site>` — tells Frappe which site's database to resolve
  against, independent of the `Host` header (`frappe/app.py:get_site()`).

Phase 3 adds the run loop's three real capabilities: `get_run_config` (agent/model/
tools/prompt for one run), `dispatch_tool` (execute one Frappe-touching tool call as
the originating user — ADR 0003), and `persist_run_result`/`fail_run` (the
persistence-via-callback pattern that replaces `flow`'s `stream_with_persistence`).
Each of `dispatch_tool`/`get_run_config` additionally carries the **acting user** as
an explicit body/query parameter, not inferred from a session — the service has none.
Frappe scopes every permission check for that call to this user via `frappe.set_user`.
"""

from __future__ import annotations

from typing import Any

import httpx

from frappe_ai.service.config import ServiceSettings

GET_SERVICE_CONFIG_METHOD = "frappe_ai.api.service.get_service_config"
GET_RUN_CONFIG_METHOD = "frappe_ai.api.service.get_run_config"
DISPATCH_TOOL_METHOD = "frappe_ai.api.dispatch.dispatch_tool"
DISPATCH_PLUGIN_TOOL_METHOD = "frappe_ai.api.dispatch.dispatch_plugin_tool"
PERSIST_RUN_RESULT_METHOD = "frappe_ai.api.api.persist_run_result"
FAIL_RUN_METHOD = "frappe_ai.api.api.fail_run"
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
		"""Fetch `AI Settings` service-relevant fields from Frappe.

		Returns:
			dict[str, Any]: The subset of `AI Settings` the service needs, as returned
				by `frappe_ai.api.service.get_service_config`.

		Raises:
			FrappeClientError: If the call fails or Frappe rejects the shared secret.
		"""
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
		"""Fetch everything needed to build and run one turn's Agno agent.

		Args:
			run (str): `AI Run` name.
			user (str): The Frappe user this run belongs to — every permission
				check on the Frappe side runs as this user.

		Returns:
			dict[str, Any]: See `frappe_ai.api.service.get_run_config`'s docstring
				for the exact shape (agent/model/tools/messages/config_snapshot).

		Raises:
			FrappeClientError: If the call fails, the secret is rejected, or Frappe
				refuses the run (wrong owner, not active, disabled model, etc.).
		"""
		return await self._get_json(GET_RUN_CONFIG_METHOD, params={"run": run, "user": user})

	async def dispatch_tool(self, tool: str, user: str, arguments: dict[str, Any] | None = None, run: str | None = None) -> dict[str, Any]:
		"""Execute one Frappe-touching tool call as `user` (ADR 0003).

		Args:
			tool (str): `AI Tool` slug.
			user (str): The Frappe user originating this call.
			arguments (dict[str, Any] | None): Keyword arguments for the tool.

		Returns:
			dict[str, Any]: `{"result": ...}` on success or `{"error": "..."}` if the
				tool itself raised — both are normal outcomes the run loop feeds back
				to the model, not client-level failures.

		Raises:
			FrappeClientError: If the call fails at the transport/auth level (not a
				tool-level error, which comes back as `{"error": ...}` in the 200).
		"""
		return await self._post_json(
			DISPATCH_TOOL_METHOD,
			json={"tool": tool, "user": user, "arguments": arguments or {}, "run": run},
			timeout=TOOL_DISPATCH_TIMEOUT_SECONDS,
		)

	async def dispatch_plugin_tool(self, tool: str, user: str, arguments: dict[str, Any] | None = None, run: str | None = None) -> dict[str, Any]:
		"""Execute a same-site Assistant Core tool through its registry."""
		return await self._post_json(
			DISPATCH_PLUGIN_TOOL_METHOD,
			json={"tool": tool, "user": user, "arguments": arguments or {}, "run": run},
			timeout=TOOL_DISPATCH_TIMEOUT_SECONDS,
		)

	async def persist_run_result(self, run: str, result: dict[str, Any]) -> dict[str, Any]:
		"""Persist a finished/paused run segment (the `Done`-event callback).

		Args:
			run (str): `AI Run` name.
			result (dict[str, Any]): See `AIRun.apply_result`'s docstring for the shape.

		Returns:
			dict[str, Any]: `{"status": <new AI Run status>}`.

		Raises:
			FrappeClientError: If the call fails or Frappe rejects the shared secret.
		"""
		return await self._post_json(PERSIST_RUN_RESULT_METHOD, json={"run": run, "result": result})

	async def fail_run(self, run: str, error: str) -> dict[str, Any]:
		"""Mark a run Failed (exception mid-run, or client disconnect before `Done`).

		Args:
			run (str): `AI Run` name.
			error (str): Error message.

		Returns:
			dict[str, Any]: `{"status": "Failed"}`.

		Raises:
			FrappeClientError: If the call fails or Frappe rejects the shared secret.
		"""
		return await self._post_json(FAIL_RUN_METHOD, json={"run": run, "error": error})

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
