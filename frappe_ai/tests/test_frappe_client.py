# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for the FastAPI service's HTTP client back into Frappe."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from frappe_ai.service.config import ServiceSettings
from frappe_ai.service.frappe_client import (
	DISPATCH_TOOL_METHOD,
	TOOL_DISPATCH_TIMEOUT_SECONDS,
	FrappeClient,
)


class TestFrappeClient(unittest.IsolatedAsyncioTestCase):
	async def test_dispatch_tool_uses_long_timeout(self):
		client = FrappeClient(
			ServiceSettings(
				service_secret="secret",
				site="test.local",
				frappe_url="http://127.0.0.1:8000",
			)
		)
		client._post_json = AsyncMock(return_value={"result": "ok"})

		result = await client.dispatch_tool("load_spec_review_context", "Administrator", {"enquiry": "E-1"})

		self.assertEqual(result, {"result": "ok"})
		client._post_json.assert_awaited_once_with(
			DISPATCH_TOOL_METHOD,
			json={
				"tool": "load_spec_review_context",
				"user": "Administrator",
				"arguments": {"enquiry": "E-1"},
			},
			timeout=TOOL_DISPATCH_TIMEOUT_SECONDS,
		)


if __name__ == "__main__":
	unittest.main()
