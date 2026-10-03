# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Persist run status and transcript updates from service callbacks.

`apply_result` receives the segment's full transcript as JSON-compatible data.
It appends only messages beyond the count already stored in the session;
`persist_run_result` and `fail_run` save outcomes over HTTP."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

#: A run in one of these states is finished; late or duplicate service callbacks must not
#: rewrite it (a user's Stop must stay a Stop even if the model finishes afterwards).
TERMINAL_STATUSES = ("Completed", "Failed")

JSON_FIELDS = ("tool_calls", "questions", "usage", "config_snapshot", "budget_usage", "approvals")


class RunAlreadyFinished(frappe.ValidationError):
	"""Raised when a result or failure is applied to a run that is already Completed/Failed."""


class AIRun(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		approvals: DF.JSON | None
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
		segment_started_at: DF.Datetime | None
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
		"""Apply a run segment and append its new messages to the session.

		Resume accumulates iterations and usage across segments. Raise
		`RunAlreadyFinished` for terminal runs."""
		self._assert_not_finished()
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

	def _assert_not_finished(self) -> None:
		"""Refuse to change a run that is already Completed or Failed."""
		if self.status in TERMINAL_STATUSES:
			raise RunAlreadyFinished(_("Run {0} is already {1}.").format(self.name, self.status))

	def mark_failed(self, error: str) -> None:
		"""Mark a run as failed with the given error message.

		Raises:
			RunAlreadyFinished: If the run is already Completed or Failed.
		"""
		self._assert_not_finished()
		self.status = "Failed"
		self.error = str(error)[:5000]
		self.save(ignore_permissions=True)


def approval_hash(arguments: dict[str, Any] | None) -> str:
	"""Stable digest of a tool call's arguments, so an approval covers exactly the
	arguments the user was shown and nothing else."""
	canonical = json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_approvals(run: "AIRun", answers: dict[str, Any]) -> list[str]:
	"""Record user-approved call IDs against the pending tool and argument digest.

	Only the user-authenticated resume path may grant these approvals."""
	questions = {q.get("key"): q for q in json.loads(run.questions) if isinstance(q, dict)} if run.questions else {}
	approvals = json.loads(run.approvals) if run.approvals else {}
	recorded = []
	for call_id, answer in answers.items():
		question = questions.get(call_id)
		if answer != "Approve" or question is None:
			continue
		approvals[call_id] = {"tool": question.get("name"), "args_hash": approval_hash(question.get("arguments"))}
		recorded.append(call_id)
	run.db_set("approvals", json.dumps(approvals), update_modified=False)
	return recorded


def consume_approval(run: str, call_id: str | None, tool: str, arguments: dict[str, Any] | None) -> bool:
	"""Use up the approval for `call_id`, if one exists for exactly this tool and arguments.

	Single use: a matching approval is removed, so an approved call cannot be replayed.
	"""
	if not call_id:
		return False
	doc = frappe.get_doc("AI Run", run, for_update=True)
	approvals = json.loads(doc.approvals) if doc.approvals else {}
	approval = approvals.get(call_id)
	if not approval or approval.get("tool") != tool or approval.get("args_hash") != approval_hash(arguments):
		return False
	approvals.pop(call_id)
	doc.db_set("approvals", json.dumps(approvals), update_modified=False)
	return True


def assert_run_owner(run) -> None:
	"""Require ownership or write permission before bypassing run permissions."""
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
	"""Insert a Running run linked to its session and config snapshot."""
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
			"segment_started_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	return doc


def _new_messages_for_session(session: str, full_transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Return messages produced by this run, excluding the session's prior non-system history.

	`_messages()` in chat.py filters system rows from full_transcript, so the slice
	offset must count only non-system existing messages to stay aligned.
	"""
	existing = frappe.db.count("AI Session Message", {"parent": session, "role": ["!=", "system"]})
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
