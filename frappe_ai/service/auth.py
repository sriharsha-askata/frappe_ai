# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Sign and verify short-lived tokens bound to a run, session, and user.

Frappe signs `(run, session, user, expiry)` with the site-config secret;
FastAPI verifies the HMAC locally before checking run status with Frappe
(ADR 0004)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 300
_FIELD_SEPARATOR = "\x1f"  # unit separator — won't collide with run/session/user names


class InvalidRunToken(Exception):
	"""Raised when a run token fails signature verification or has expired."""


@dataclass(frozen=True)
class RunTokenPayload:
	"""Verified run, session, user, and expiry encoded in a run token."""

	run: str
	session: str
	user: str
	expiry: int


def _signing_string(run: str, session: str, user: str, expiry: int) -> str:
	"""Build the canonical, field-separated token payload for signing."""
	return _FIELD_SEPARATOR.join((run, session, user, str(expiry)))


def _sign(secret: str, message: str) -> str:
	"""Return the hex-encoded HMAC-SHA256 digest of `message`."""
	return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_run_token(
	run: str,
	session: str,
	user: str,
	secret: str,
	ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
	"""Mint a base64url HMAC token for one run, session, and user.

	The TTL covers stream setup, not stream duration. Encoding keeps the internal
	field separator out of the HTTP Authorization header."""
	expiry = int(time.time()) + ttl_seconds
	signature = _sign(secret, _signing_string(run, session, user, expiry))
	raw = _FIELD_SEPARATOR.join((run, session, user, str(expiry), signature))
	return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def verify_run_token(token: str, secret: str) -> RunTokenPayload:
	"""Return verified token contents or raise `InvalidRunToken` for bad or expired tokens."""
	try:
		padding = "=" * (-len(token) % 4)
		raw = base64.urlsafe_b64decode(token + padding).decode("utf-8")
	except Exception:
		raise InvalidRunToken("Malformed token")
	parts = raw.split(_FIELD_SEPARATOR)
	if len(parts) != 5:
		raise InvalidRunToken("Malformed token")

	run, session, user, expiry_str, signature = parts

	try:
		expiry = int(expiry_str)
	except ValueError:
		raise InvalidRunToken("Malformed token expiry")

	expected_signature = _sign(secret, _signing_string(run, session, user, expiry))
	if not hmac.compare_digest(signature, expected_signature):
		raise InvalidRunToken("Signature mismatch")

	if time.time() >= expiry:
		raise InvalidRunToken("Token expired")

	return RunTokenPayload(run=run, session=session, user=user, expiry=expiry)
