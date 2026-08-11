# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Run-token HMAC primitive.

Per ADR 0004: Frappe mints a short-lived, single-run token as an HMAC over
`(run, session, user, expiry)`, signed with `site_config.json`'s `frappe_ai_service_secret`. FastAPI
verifies the signature locally (no round-trip needed for that part) before
confirming the run is still `Running` with Frappe.

`AI Run`/`AI Session` don't exist until Phase 3, so `mint_run_token`/`verify_run_token`
operate on bare `run`/`session`/`user` strings rather than DocType links — Phase 3's
`start_run` calls `mint_run_token` with real document names once those exist. The
signing/verification logic itself does not change between now and then.

Deliberately stdlib-only (`hmac` + `hashlib`), not a third-party JWT library — this is
a small, fully-specified primitive (fixed field set, no algorithm negotiation, no
header/claims ceremony) where a JWT dependency would add surface area without adding
capability.
"""

from __future__ import annotations

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
	"""Decoded, verified contents of a run token.

	Attributes:
		run (str): `AI Run` name the token is bound to.
		session (str): `AI Session` name the run belongs to.
		user (str): Frappe user the run was started by.
		expiry (int): Unix timestamp after which the token is invalid.
	"""

	run: str
	session: str
	user: str
	expiry: int


def _signing_string(run: str, session: str, user: str, expiry: int) -> str:
	"""Build the canonical string signed and verified for a run token.

	Args:
		run (str): `AI Run` name.
		session (str): `AI Session` name.
		user (str): Acting user.
		expiry (int): Unix timestamp expiry.

	Returns:
		str: Field-separated canonical payload string.
	"""
	return _FIELD_SEPARATOR.join((run, session, user, str(expiry)))


def _sign(secret: str, message: str) -> str:
	"""HMAC-SHA256 sign `message` with `secret`, hex-encoded.

	Args:
		secret (str): Shared secret (`site_config.json`'s `frappe_ai_service_secret`).
		message (str): Canonical signing string.

	Returns:
		str: Hex-encoded HMAC digest.
	"""
	return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_run_token(
	run: str,
	session: str,
	user: str,
	secret: str,
	ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
	"""Mint a short-lived, single-run HMAC token.

	Args:
		run (str): `AI Run` name this token is bound to.
		session (str): `AI Session` name the run belongs to.
		user (str): Frappe user the run was started by.
		secret (str): Shared secret (`site_config.json`'s `frappe_ai_service_secret`).
		ttl_seconds (int): Seconds until the token expires. Defaults to 300s,
			covering stream setup only, not stream duration.

	Returns:
		str: Opaque token string of the form `<run>.<session>.<user>.<expiry>.<signature>`.
	"""
	expiry = int(time.time()) + ttl_seconds
	signature = _sign(secret, _signing_string(run, session, user, expiry))
	return _FIELD_SEPARATOR.join((run, session, user, str(expiry), signature))


def verify_run_token(token: str, secret: str) -> RunTokenPayload:
	"""Verify a run token's signature and expiry.

	Args:
		token (str): Token string produced by `mint_run_token`.
		secret (str): Shared secret to verify against (`site_config.json`'s `frappe_ai_service_secret`).

	Returns:
		RunTokenPayload: The verified, decoded token contents.

	Raises:
		InvalidRunToken: If the token is malformed, tampered with, or expired.
	"""
	parts = token.split(_FIELD_SEPARATOR)
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
