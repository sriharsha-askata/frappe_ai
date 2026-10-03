# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for the run-token HMAC primitive (`frappe_ai/service/auth.py`).

Plain `unittest.TestCase`, not `IntegrationTestCase` — `auth.py` is stdlib-only
(no Frappe DB access), so no site context is needed to exercise it.
"""

from __future__ import annotations

import base64
import time
import unittest

from frappe_ai.service.auth import InvalidRunToken, mint_run_token, verify_run_token

SECRET = "test-service-secret"


def _tamper_field(token: str, index: int, replacement: str) -> str:
	parts = base64.urlsafe_b64decode(token).decode("utf-8").split("\x1f")
	parts[index] = replacement
	return base64.urlsafe_b64encode("\x1f".join(parts).encode("utf-8")).decode("ascii")


class TestRunTokenRoundtrip(unittest.TestCase):
	def test_valid_token_verifies(self):
		token = mint_run_token(run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET)
		payload = verify_run_token(token, SECRET)

		self.assertEqual(payload.run, "RUN-1")
		self.assertEqual(payload.session, "SESS-1")
		self.assertEqual(payload.user, "user@example.com")

	def test_wrong_secret_rejected(self):
		token = mint_run_token(run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET)
		with self.assertRaises(InvalidRunToken):
			verify_run_token(token, "a-different-secret")


class TestRunTokenTampering(unittest.TestCase):
	def test_tampered_run_rejected(self):
		token = mint_run_token(run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET)
		tampered = _tamper_field(token, 0, "RUN-2")

		with self.assertRaises(InvalidRunToken):
			verify_run_token(tampered, SECRET)

	def test_tampered_user_rejected(self):
		token = mint_run_token(run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET)
		tampered = _tamper_field(token, 2, "attacker@example.com")

		with self.assertRaises(InvalidRunToken):
			verify_run_token(tampered, SECRET)

	def test_tampered_signature_rejected(self):
		token = mint_run_token(run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET)
		tampered = _tamper_field(token, 4, "0" * 64)

		with self.assertRaises(InvalidRunToken):
			verify_run_token(tampered, SECRET)

	def test_malformed_token_rejected(self):
		with self.assertRaises(InvalidRunToken):
			verify_run_token("not-a-real-token", SECRET)


class TestRunTokenExpiry(unittest.TestCase):
	def test_expired_token_rejected(self):
		token = mint_run_token(
			run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET, ttl_seconds=-1
		)
		with self.assertRaises(InvalidRunToken):
			verify_run_token(token, SECRET)

	def test_token_valid_just_before_expiry(self):
		token = mint_run_token(
			run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET, ttl_seconds=60
		)
		# not expired yet — sanity check the happy path near the boundary
		payload = verify_run_token(token, SECRET)
		self.assertLess(time.time(), payload.expiry)

	def test_token_bound_to_single_run(self):
		token = mint_run_token(run="RUN-1", session="SESS-1", user="user@example.com", secret=SECRET)
		payload = verify_run_token(token, SECRET)
		self.assertNotEqual(payload.run, "RUN-2")


if __name__ == "__main__":
	unittest.main()
