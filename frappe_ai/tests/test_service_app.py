# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for the FastAPI service skeleton (`frappe_ai/service/main.py`).

Uses `fastapi.testclient.TestClient` (sync, no separate event loop) rather than
`IntegrationTestCase` — this exercises the ASGI app in isolation, the same way
Phase 2's `main.py` runs in its own process with no `frappe.init`/`frappe.connect`.

`frappe_ai.service.main` reads its bootstrap settings from `site_config.json` at
*import* time (`config.load_settings()` — see ADR 0011), by reading a real file off
disk (deliberately: `config.py` never calls `frappe.init`/`frappe.connect`). A
throwaway site directory is created under a temp `sites/` root before the module
import below, with `FRAPPE_AI_SITES_PATH`/`FRAPPE_AI_SITE` pointed at it — nothing
here touches this bench's real sites.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_tmp_sites_dir = tempfile.mkdtemp(prefix="frappe_ai_test_sites_")
_test_site = "test.local"
(Path(_tmp_sites_dir) / _test_site).mkdir(parents=True, exist_ok=True)
(Path(_tmp_sites_dir) / _test_site / "site_config.json").write_text(
	json.dumps({"frappe_ai_service_secret": "test-service-secret"})
)

os.environ["FRAPPE_AI_SITES_PATH"] = _tmp_sites_dir
os.environ["FRAPPE_AI_SITE"] = _test_site
os.environ.setdefault("FRAPPE_AI_FRAPPE_URL", "http://127.0.0.1:8000")

from fastapi.testclient import TestClient  # noqa: E402

from frappe_ai.service.auth import mint_run_token  # noqa: E402
from frappe_ai.service.main import app, settings  # noqa: E402
from frappe_ai.service.routes.chat import MAX_STORED_TOOL_CONTENT_CHARS, _trim_stored_tool_content, _to_agno_messages  # noqa: E402


class TestHealthEndpoint(unittest.TestCase):
	def setUp(self):
		self.client = TestClient(app)

	def test_health_returns_200_without_auth(self):
		with patch("frappe_ai.service.main.frappe_client.ping", new=AsyncMock(return_value=True)):
			response = self.client.get("/health")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["status"], "ok")

	def test_health_reports_frappe_unreachable(self):
		with patch("frappe_ai.service.main.frappe_client.ping", new=AsyncMock(return_value=False)):
			response = self.client.get("/health")
		self.assertEqual(response.status_code, 200)
		self.assertFalse(response.json()["frappe_reachable"])


class TestProtectedStreamPlaceholder(unittest.TestCase):
	def setUp(self):
		self.client = TestClient(app)

	def test_missing_token_rejected(self):
		response = self.client.post("/stream/RUN-1")
		self.assertEqual(response.status_code, 401)

	def test_malformed_bearer_rejected(self):
		response = self.client.post("/stream/RUN-1", headers={"Authorization": "not-bearer-format"})
		self.assertEqual(response.status_code, 401)

	def test_forged_token_rejected(self):
		token = mint_run_token(
			run="RUN-1", session="SESS-1", user="user@example.com", secret="wrong-secret"
		)
		response = self.client.post("/stream/RUN-1", headers={"Authorization": f"Bearer {token}"})
		self.assertEqual(response.status_code, 401)

	def test_expired_token_rejected(self):
		token = mint_run_token(
			run="RUN-1",
			session="SESS-1",
			user="user@example.com",
			secret=settings.service_secret,
			ttl_seconds=-1,
		)
		response = self.client.post("/stream/RUN-1", headers={"Authorization": f"Bearer {token}"})
		self.assertEqual(response.status_code, 401)

	def test_valid_token_for_different_run_rejected(self):
		token = mint_run_token(
			run="RUN-1", session="SESS-1", user="user@example.com", secret=settings.service_secret
		)
		response = self.client.post("/stream/RUN-2", headers={"Authorization": f"Bearer {token}"})
		self.assertEqual(response.status_code, 401)

	def test_valid_token_accepted(self):
		token = mint_run_token(
			run="RUN-1", session="SESS-1", user="user@example.com", secret=settings.service_secret
		)
		response = self.client.post("/stream/RUN-1", headers={"Authorization": f"Bearer {token}"})
		self.assertEqual(response.status_code, 200)


class TestCORSConfig(unittest.TestCase):
	def test_cors_origins_not_wildcard(self):
		# A mutating/authenticated API should never allow `*`.
		self.assertNotIn("*", settings.cors_origins)
		self.assertTrue(settings.cors_origins)


class TestMessageConversion(unittest.TestCase):
	def test_system_message_is_preserved_for_provider_input(self):
		messages = _to_agno_messages(
			[
				{"role": "system", "content": "You are terse."},
				{"role": "user", "content": "hello"},
			],
			questions_by_id={},
			redirect_answers={},
		)

		self.assertEqual(messages[0].role, "system")
		self.assertEqual(messages[0].content, "You are terse.")
		self.assertEqual(messages[1].role, "user")

	def test_trim_stored_tool_content_caps_large_payloads(self):
		row = {"role": "tool", "content": "x" * (MAX_STORED_TOOL_CONTENT_CHARS + 10)}

		_trim_stored_tool_content(row)

		self.assertLess(len(row["content"]), MAX_STORED_TOOL_CONTENT_CHARS + 100)
		self.assertIn("truncated 10 chars", row["content"])


if __name__ == "__main__":
	unittest.main()
