"""Per-run tool and mutation budget enforcement (ADR 0008)."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime


class BudgetExceeded(frappe.ValidationError):
	pass


def consume(run: str | None, *, mutation: bool = False, records: int = 1) -> None:
	# Fail closed. A tool call that cannot be attributed to a run cannot be
	# counted against that run's budget, so allowing it through would make the
	# budget optional for any caller that simply omits `run` — which is exactly
	# how the confirmation-approve path silently escaped accounting.
	if not run:
		raise BudgetExceeded(_("Tool dispatch requires a run to account against."))
	# Lock the run row for this read-modify-write. Without the lock two concurrent
	# tool calls read the same counters, each increments from that same value, and
	# the second write discards the first — so a model issuing parallel tool calls,
	# the case budgets most need to bound, silently exceeds them.
	row = frappe.db.get_value(
		"AI Run",
		run,
		["status", "config_snapshot", "budget_usage", "creation", "segment_started_at"],
		as_dict=True,
		for_update=True,
	)
	if not row:
		raise BudgetExceeded(_("Run {0} does not exist.").format(run))
	if row.status not in ("Running", "Paused"):
		raise BudgetExceeded(_("Run is no longer active."))
	snapshot = json.loads(row.config_snapshot or "{}")
	limits = snapshot.get("budgets") or snapshot
	# Active time in the current segment: a run resumed after a long wait for approval
	# starts a fresh segment (`resume_run`), so the human's wait does not count.
	segment_start = row.segment_started_at or row.creation
	if segment_start and (now_datetime() - get_datetime(segment_start)).total_seconds() > limits.get("max_runtime_seconds", 600):
		raise BudgetExceeded(_("Run runtime budget exceeded."))
	usage = json.loads(row.budget_usage or "{}")
	usage.setdefault("tool_calls", 0)
	usage.setdefault("mutations", 0)
	usage.setdefault("records", 0)
	usage["tool_calls"] += 1
	if mutation:
		usage["mutations"] += 1
	if records > limits.get("max_records_per_call", 100):
		raise BudgetExceeded(_("Tool call exceeds the maximum records-per-call budget."))
	if usage["tool_calls"] > limits.get("max_tool_calls", 50):
		raise BudgetExceeded(_("Tool-call budget exceeded for this run."))
	if usage["mutations"] > limits.get("max_mutations", 20):
		raise BudgetExceeded(_("Mutation budget exceeded for this run."))
	usage["records"] += records
	frappe.db.set_value("AI Run", run, "budget_usage", json.dumps(usage), update_modified=False)
