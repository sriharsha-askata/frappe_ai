# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Run` — ported from `flow`'s `Flow Run`
(see `apps/flow/flow/flow/doctype/flow_run/flow_run.py`).

`apply_result` takes a plain dict here, not a `RunResult` dataclass — the FastAPI
service is a separate process, so a run's result crosses the wire as JSON via the
`frappe_ai.api.api.persist_run_result` callback, not an in-process object. Shape:

	{
		"status": "Completed" | "Paused" | "Failed",
		"iterations": int,          # this segment's iteration count (accumulated onto the row)
		"output": str | None,
		"tool_calls": [{"id", "name", "arguments"}, ...],
		"questions": [{"key", ...}, ...] | None,   # only when status == "Paused"
		"usage": {"prompt_tokens": int, "completion_tokens": int, ...},  # this segment's usage
		"messages": [{"role", "content", "tool_call_id"?, "tool_calls"?}, ...],  # FULL transcript
	}

`messages` is the full transcript as the service sees it (system + history + this
segment) — `apply_result` diffs it against the session's already-stored message count
and appends only the delta, exactly as `flow`'s `_new_messages_for_session` does.

`stream_with_persistence`'s WSGI commit choreography is deliberately not ported — see
`001-architecture.md` §8. FastAPI's persistence-via-explicit-HTTP-callback pattern
replaces it: the service calls `persist_run_result`/`fail_run` explicitly on `Done`/
exception/disconnect; there is no generator-iterated-after-response-returns problem to
work around.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

JSON_FIELDS = ("tool_calls", "questions", "usage", "config_snapshot", "budget_usage")


class AIRun(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		config_snapshot: DF.JSON | None
		error: DF.LongText | None
		feedback_comment: DF.SmallText | None
		feedback_rating: DF.Literal["", "Up", "Down"]
		input: DF.LongText | None
		iterations: DF.Int
		output: DF.LongText | None
		questions: DF.JSON | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		session: DF.Link
		source: DF.Literal["Manual", "Trigger"]
		status: DF.Literal["Running", "Paused", "Completed", "Failed"]
		trigger: DF.Link | None
		tool_calls: DF.JSON | None
		usage: DF.JSON | None
	# end: auto-generated types

	def validate(self):
		self._validate_json_fields()
		self._validate_status_invariants()

	def _validate_json_fields(self):
		for fieldname in JSON_FIELDS:
			value = self.get(fieldname)
			if value in (None, ""):
				continue
			if not isinstance(value, str):
				continue
			try:
				json.loads(value)
			except (TypeError, ValueError):
				frappe.throw(
					_("{0} must be valid JSON.").format(fieldname),
					title=_("Invalid JSON"),
				)

	def _validate_status_invariants(self):
		if self.status == "Paused" and not _json_has_items(self.questions):
			frappe.throw(_("Paused runs must have at least one pending question."))
		if self.status == "Failed" and not self.error:
			frappe.throw(_("Failed runs must include an error message."))

	def apply_result(self, result: dict[str, Any]) -> None:
		"""Update this row to reflect a (re-)executed run. The new messages produced by
		this segment are appended to the parent Session's transcript.

		Resume re-invokes this on the same run: `iterations` and `usage` **accumulate**
		here rather than overwrite, so a paused-then-resumed run's counters reflect the
		whole run, not just the latest segment.
		"""
		self.status = result.get("status") or "Completed"
		self.iterations = (self.iterations or 0) + int(result.get("iterations") or 0)
		self.output = result.get("output")
		self.tool_calls = _dump_json(result.get("tool_calls") or [])
		self.questions = _dump_json(result.get("questions")) if self.status == "Paused" else None
		self.usage = _dump_json(_merge_usage(self.usage, result.get("usage") or {}))
		if self.status != "Failed":
			self.error = None
		self.save(ignore_permissions=True)

		new_messages = _new_messages_for_session(self.session, result.get("messages") or [])
		if new_messages:
			session = frappe.get_doc("AI Session", self.session)
			session.append_run_messages(new_messages, run=self.name)

	def mark_failed(self, error: str) -> None:
		"""Mark a run as failed with the given error message."""
		self.status = "Failed"
		self.error = str(error)[:5000]
		self.save(ignore_permissions=True)


def assert_run_owner(run) -> None:
	"""Owner match or `write` permission, else `frappe.PermissionError`.

	Authorization chokepoint: `AIRun.apply_result`/`mark_failed` (and `AISession`'s
	equivalent message-append) use `ignore_permissions=True`, so this check is the only
	thing standing between a user and someone else's run.

	Args:
		run: An `AI Run` name (str) or already-loaded document.
	"""
	doc = frappe.get_doc("AI Run", run) if isinstance(run, str) else run
	if doc.owner == frappe.session.user:
		return
	if frappe.has_permission("AI Run", "write", doc):
		return
	frappe.throw(_("Not permitted to use this run."), frappe.PermissionError)


def create_run(
	*,
	source: str,
	input: str | None,
	session: str,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	trigger: str | None = None,
	config_snapshot: dict[str, Any] | None = None,
) -> "AIRun":
	"""Create a new `AI Run` row in the Running state. Every run belongs to an `AI
	Session` (which carries the transcript and agent linkage).

	Args:
		source (str): "Manual" or "Trigger".
		input (str | None): The user input that started the run.
		session (str): `AI Session` name this run belongs to.
		reference_doctype (str | None): For Trigger runs, the DocType that fired it.
		reference_name (str | None): For Trigger runs, the document that fired it.
		config_snapshot (dict[str, Any] | None): `AI Agent._snapshot()` output.

	Returns:
		AIRun: The inserted, Running run.
	"""
	doc = frappe.get_doc(
		{
			"doctype": "AI Run",
			"source": source,
			"trigger": trigger,
			"input": input,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"session": session,
			"config_snapshot": _dump_json(config_snapshot) if config_snapshot else None,
			"status": "Running",
		}
	).insert(ignore_permissions=True)
	return doc


def _new_messages_for_session(session: str, full_transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Return new messages produced by this run, excluding the session's prior history."""
	existing = frappe.db.count("AI Session Message", {"parent": session})
	return list(full_transcript[existing:])


def _dump_json(value: Any) -> str | None:
	if value is None:
		return None
	return json.dumps(value, default=str)


def _merge_usage(existing: str | None, new: dict[str, int]) -> dict[str, int]:
	"""Add token counts from a (resumed) segment onto whatever the run already recorded."""
	merged = json.loads(existing) if existing else {}
	for key, value in new.items():
		merged[key] = merged.get(key, 0) + value
	return merged


def _json_has_items(value: Any) -> bool:
	if not value:
		return False
	try:
		parsed = json.loads(value) if isinstance(value, str) else value
	except (TypeError, ValueError):
		return False
	return bool(parsed)
