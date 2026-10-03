# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""FastAPI service entrypoint.

Provides a health check and an authenticated `/stream/{run}` route. After run-token
verification, `frappe_ai.service.routes.chat` builds the Agno agent and streams
the run. The service communicates with Frappe through its HTTP client.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from frappe_ai.service.auth import InvalidRunToken, verify_run_token
from frappe_ai.service.config import load_settings
from frappe_ai.service.frappe_client import FrappeClient
from frappe_ai.service.routes.chat import stream_chat

logger = logging.getLogger("frappe_ai.service")

settings = load_settings()
frappe_client = FrappeClient(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
	"""Log service startup and shutdown."""
	logger.info("frappe_ai service starting up (site=%s, frappe_url=%s)", settings.site, settings.frappe_url)
	yield
	logger.info("frappe_ai service shutting down")


app = FastAPI(title="frappe_ai service", lifespan=lifespan)

app.add_middleware(
	CORSMiddleware,
	allow_origins=settings.cors_origins,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
	"""Liveness probe. No auth required.

	Returns:
		dict: `{"status": "ok", "frappe_reachable": bool}`.
	"""
	frappe_reachable = await frappe_client.ping()
	return {"status": "ok", "frappe_reachable": frappe_reachable}


class StreamRunBody(BaseModel):
	"""POST body for `/stream/{run}`. Empty (or omitted) for a fresh turn; `answers`
	present when resuming a Paused run — see `001-architecture.md` §5.2."""

	answers: dict[str, Any] | None = None


@app.post("/stream/{run}")
async def stream_run(
	run: str, body: StreamRunBody = StreamRunBody(), authorization: str | None = Header(default=None)
) -> StreamingResponse:
	"""Authenticate the bearer run token and stream the turn as SSE.

	`body.answers` resumes pending questions. Return 401 for a missing, invalid,
	expired, or mismatched token; delegate the run to `stream_chat`."""
	if not authorization or not authorization.startswith("Bearer "):
		raise HTTPException(status_code=401, detail="Missing bearer run token")

	token = authorization.removeprefix("Bearer ")
	try:
		payload = verify_run_token(token, settings.service_secret)
	except InvalidRunToken as e:
		raise HTTPException(status_code=401, detail=str(e))

	if payload.run != run:
		raise HTTPException(status_code=401, detail="Token is not bound to this run")

	return StreamingResponse(
		stream_chat(run, payload.user, payload.session, frappe_client, answers=body.answers),
		media_type="text/event-stream",
		headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
	)
