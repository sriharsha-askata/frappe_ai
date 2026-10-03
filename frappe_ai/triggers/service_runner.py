# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import time

import frappe
import requests
from frappe import _
from requests import RequestException

from frappe_ai.api._service_url import get_service_url
from frappe_ai.service.auth import mint_run_token

TERMINAL_STATUS_WAIT_SECONDS = 2.0
TERMINAL_STATUS_POLL_SECONDS = 0.1


def _run_via_service(run: str, session: str, user: str) -> None:
	"""Start the FastAPI run and consume its SSE stream until terminal state."""
	secret = frappe.conf.get("frappe_ai_service_secret")
	if not secret:
		frappe.throw(
			_("frappe_ai_service_secret is not set in site_config.json."), title=_("Service Not Configured")
		)
	service_url = get_service_url()
	stream_timeout = frappe.get_cached_value("AI Settings", "AI Settings", "stream_timeout") or 600
	token = mint_run_token(run=run, session=session, user=user, secret=secret)
	frappe.log_error(
		title="frappe_ai Service Stream: POST",
		message=frappe.as_json(
			{
				"run": run,
				"session": session,
				"user": user,
				"service_base_url": service_url,
				"stream_timeout": stream_timeout,
			}
		),
	)

	try:
		response = requests.post(
			f"{service_url}/stream/{run}",
			headers={"Authorization": f"Bearer {token}"},
			json={},
			stream=True,
			timeout=stream_timeout,
		)
		response.raise_for_status()
		last_event = ""
		saw_terminal_event = False
		terminal_event = ""
		terminal_payload = ""
		for line in response.iter_lines(decode_unicode=True):
			if not line:
				continue
			if line.startswith("event: "):
				last_event = line.removeprefix("event: ").strip()
				continue
			if line.startswith("data: ") and last_event in {"done", "error"}:
				saw_terminal_event = True
				terminal_event = last_event
				terminal_payload = line.removeprefix("data: ").strip()
				frappe.log_error(
					title="frappe_ai Service Stream: terminal event",
					message=frappe.as_json(
						{"run": run, "session": session, "event": last_event, "payload": terminal_payload}
					),
				)
				break
		if saw_terminal_event:
			status = _wait_for_terminal_run_status(run)
			if terminal_event == "done" and status in {"Completed", "Paused"}:
				return
			if terminal_event == "error" and status == "Failed":
				return
			frappe.log_error(
				title="frappe_ai Service Stream: terminal event without terminal db state",
				message=frappe.as_json(
					{
						"run": run,
						"session": session,
						"event": terminal_event,
						"status": status,
						"payload": terminal_payload,
					}
				),
			)
			error_message = _stream_error_message(terminal_payload) if terminal_event == "error" else None
			raise RuntimeError(
				error_message
				or _("frappe_ai service emitted {0} but the run is still {1}.").format(
					terminal_event, status or _("missing")
				)
			)
		if not saw_terminal_event:
			status = frappe.db.get_value("AI Run", run, "status")
			if status in {"Completed", "Failed", "Paused"}:
				frappe.log_error(
					title="frappe_ai Service Stream: terminal db state",
					message=frappe.as_json({"run": run, "session": session, "status": status}),
				)
				return
			frappe.log_error(
				title="frappe_ai Service Stream: missing terminal event",
				message=frappe.as_json({"run": run, "session": session, "status": status}),
			)
			raise RuntimeError(
				_(
					"frappe_ai service stream ended before emitting a terminal event and before the run reached a terminal state."
				)
			)
	except RequestException as exc:
		status = frappe.db.get_value("AI Run", run, "status")
		if status in {"Completed", "Failed", "Paused"}:
			# The service can persist the terminal run state successfully and still lose the
			# outbound chunked response before bench sees the final SSE frame. In that case,
			# prefer the persisted run state over the transport error.
			frappe.log_error(
				title="frappe_ai Service Stream: transport error after terminal db state",
				message=frappe.as_json({"run": run, "session": session, "status": status, "error": str(exc)}),
			)
			return
		frappe.log_error(
			title="frappe_ai Service Stream: transport error",
			message=frappe.as_json({"run": run, "session": session, "status": status, "error": str(exc)}),
		)
		raise RuntimeError(
			_("frappe_ai service stream failed before the run reached a terminal state: {0}").format(exc)
		)


def _stream_error_message(payload: str) -> str | None:
	if not payload:
		return None
	try:
		data = json.loads(payload)
	except ValueError:
		return payload
	return data.get("message") or data.get("error") or payload


def _wait_for_terminal_run_status(run: str) -> str | None:
	deadline = time.monotonic() + TERMINAL_STATUS_WAIT_SECONDS
	frappe.db.commit()
	status = frappe.db.get_value("AI Run", run, "status")
	while status not in {"Completed", "Failed", "Paused"} and time.monotonic() < deadline:
		time.sleep(TERMINAL_STATUS_POLL_SECONDS)
		frappe.db.commit()
		status = frappe.db.get_value("AI Run", run, "status")
	return status
